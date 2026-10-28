"""GraphQLClient (design 10.4): POST to the gateway, ``X-ExperimentalApi`` for experimental fields, GraphQL errors mapped to typed errors."""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from ...core.errors import (
    AtlassianError,
    AuthenticationError,
    InvalidRequestError,
    NotFoundError,
    PermissionDeniedError,
    PolicyDeniedError,
)
from ...core.http import HttpTransport, RequestSpec
from ...core.urls import ApiSurface

_OP_TYPE = re.compile(r"^\s*(?:#[^\n]*\n\s*)*(query|mutation|subscription)\b", re.IGNORECASE)


def operation_type(document: str) -> str:
    """``query`` for anonymous ``{ ... }`` documents; otherwise the first operation keyword."""
    m = _OP_TYPE.match(document)
    return m.group(1).lower() if m else "query"


def map_graphql_errors(errors: Sequence[Mapping[str, Any]], *, operation: str | None, backend: str = "graphql") -> AtlassianError:
    first = errors[0]
    ext = first.get("extensions") or {}
    code = str(ext.get("classification") or ext.get("code") or ext.get("statusCode") or "").upper()
    message = "; ".join(str(e.get("message", "GraphQL error")) for e in errors)[:500]
    if code in ("NOT_FOUND", "404") or (code == "DATAFETCHINGEXCEPTION" and "not found" in message.lower()):
        err: AtlassianError = NotFoundError(message, hint="Not found OR you lack permission to see it. Verify the key/id by searching.", status=404)
    elif code in ("FORBIDDEN", "PERMISSION_DENIED", "403"):
        err = PermissionDeniedError(message, hint="The credentials lack permission for this field or object.", status=403)
    elif code in ("UNAUTHENTICATED", "UNAUTHORIZED", "401"):
        err = AuthenticationError(message, hint="Check the API token / OAuth grant for the GraphQL gateway.", status=401)
    else:
        err = InvalidRequestError(message, hint="Check the query and variables; experimental fields need experimental_apis=[...].", details={"errors": list(errors)})
    err.operation, err.backend = operation, backend
    return err


class GraphQLClient:
    def __init__(self, transport: HttpTransport, cloud_id: Callable[[], Awaitable[str]] | None = None, *, allow_mutations: bool = False) -> None:
        self.transport, self._cloud_id, self.allow_mutations = transport, cloud_id, allow_mutations

    async def cloud_id(self) -> str:
        if self._cloud_id is None:
            raise InvalidRequestError("no cloudId available for this GraphQL call", hint="Set ATLASSIAN_CLOUD_ID or ATLASSIAN_SITE.")
        return await self._cloud_id()

    async def post(self, query: str, variables: Mapping[str, Any] | None = None, *, experimental_apis: Sequence[str] = (), operation_name: str | None = None,
                   idempotent: bool = True, operation: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"query": query, "variables": dict(variables or {})}
        if operation_name:
            body["operationName"] = operation_name
        headers = {"X-ExperimentalApi": ",".join(experimental_apis)} if experimental_apis else {}
        data = await self.transport.json(RequestSpec("POST", ApiSurface.GRAPHQL, "", json=body, headers=headers, operation=operation or "graph.graphql.execute", idempotent=idempotent))
        if not isinstance(data, dict):
            raise InvalidRequestError("the GraphQL gateway returned a non-object response", hint="Check the endpoint and credentials.", operation=operation)
        return data

    async def run(self, query: str, variables: Mapping[str, Any], *, experimental_apis: Sequence[str] = (), operation: str | None = None,
                  operation_name: str | None = None) -> dict[str, Any]:
        """A stored operation: any GraphQL error is an error; returns ``data``."""
        data = await self.post(query, variables, experimental_apis=experimental_apis, operation_name=operation_name, operation=operation,
                               idempotent=operation_type(query) == "query")
        if data.get("errors"):
            raise map_graphql_errors(data["errors"], operation=operation)
        result = data.get("data")
        return result if isinstance(result, dict) else {}

    async def execute(self, query: str, variables: Mapping[str, Any] | None = None, *, experimental_apis: Sequence[str] = (), operation_name: str | None = None) -> dict[str, Any]:
        """The generic ``GraphQLExecutor``: READ access, so only queries run unless mutations were explicitly allowed. Partial data with errors is returned as is."""
        kind = operation_type(query)
        if kind != "query" and not self.allow_mutations:
            raise PolicyDeniedError(f"GraphQL {kind} documents are not allowed through the generic executor (it is a READ operation)",
                                    hint="Use the typed write operations (jira.issue.create, ...), or opt in with allow_mutations=True and WRITE access.",
                                    operation="graph.graphql.execute")
        return await self.post(query, variables, experimental_apis=experimental_apis, operation_name=operation_name, idempotent=kind == "query")
