"""The generated REST tier (design 10.1, P6): every operation of the vendored OpenAPI specs, reachable by id. The index is produced by ``tools/codegen.py``.

The tier is data, not code: ``operations_index.json`` plus this small runtime. A call builds a ``RequestSpec`` from the index entry and goes through the same
transport as the curated components (auth, retry, rate limits, redaction), so the access policy and confirmation rules apply unchanged.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

from ....capabilities import Access, Operation, Product
from ....core.errors import InvalidRequestError, NotFoundError
from ....core.http import RequestSpec
from ....core.urls import ApiSurface
from ....models.webhooks import RawResponse
from ..base import RestBase

PREFIX = "api."
_SURFACE = {"jira": ApiSurface.JIRA_PLATFORM, "agile": ApiSurface.JIRA_AGILE, "jsm": ApiSurface.JSM, "confluence_v2": ApiSurface.CONFLUENCE_V2, "confluence_v1": ApiSurface.CONFLUENCE_V1}
_PRODUCT = {"jira": Product.JIRA, "agile": Product.JIRA_SOFTWARE, "jsm": Product.JSM, "confluence_v2": Product.CONFLUENCE, "confluence_v1": Product.CONFLUENCE}
_ACCESS = {"read": Access.READ, "search": Access.SEARCH, "write": Access.WRITE, "delete": Access.DELETE, "manage": Access.MANAGE}


@dataclass(frozen=True)
class GeneratedOperation:
    id: str
    api: str                                      # jira | agile | jsm | confluence_v2 | confluence_v1
    operation_id: str
    method: str
    path: str                                     # relative to the surface base, with {placeholders}
    summary: str
    tags: tuple[str, ...]
    params: tuple[tuple[str, str, bool], ...]     # (name, in, required)
    has_body: bool
    access: str

    @property
    def surface(self) -> ApiSurface:
        return _SURFACE[self.api]

    def operation(self) -> Operation:
        """The catalog-style ``Operation`` the policy guard and router work with."""
        return Operation(self.id, _PRODUCT[self.api], _ACCESS[self.access], self.summary, idempotent=self.method in ("GET", "HEAD", "PUT", "DELETE") or self.access in ("read", "search"),
                         cloud_only=True, tags=frozenset(self.tags))

    def render_path(self, path_params: Mapping[str, Any]) -> str:
        missing = [n for n, where, req in self.params if where == "path" and n not in path_params]
        if missing:
            raise InvalidRequestError(f"{self.id}: missing path parameter(s) {', '.join(missing)}", hint=f"This operation needs: {', '.join(n for n, w, _ in self.params if w == 'path')}.",
                                      operation=self.id)
        return re.sub(r"\{(\w+)\}", lambda m: str(path_params[m.group(1)]), self.path)


def classify(method: str, path: str) -> str:
    """Access class of a generated operation (the policy decides on this): GET/HEAD read, search-like POSTs search, DELETE delete, everything else write."""
    method = method.upper()
    if method in ("GET", "HEAD"):
        return "read"
    if method == "DELETE":
        return "delete"
    if method == "POST" and re.search(r"/(search|query|parse|count|match|picker|bulkfetch)(/|$)", path):
        return "search"
    return "write"


@cache
def load_index() -> dict[str, GeneratedOperation]:
    raw = json.loads(resources.files(__package__).joinpath("operations_index.json").read_text(encoding="utf-8"))
    return {e["id"]: GeneratedOperation(e["id"], e["api"], e["operationId"], e["method"], e["path"], e.get("summary", ""), tuple(e.get("tags", ())),
                                        tuple((p[0], p[1], bool(p[2])) for p in e.get("params", ())), bool(e.get("has_body")), e["access"]) for e in raw["operations"]}


def get(op_id: str) -> GeneratedOperation:
    try:
        return load_index()[op_id]
    except KeyError:
        near = [k for k in load_index() if op_id.split(".")[-1].lower() in k.lower()][:5]
        raise NotFoundError(f"unknown generated operation {op_id!r}", hint=f"Search with atlassian_discover_operations. Similar: {near}") from None


class GeneratedRest(RestBase):
    """``GeneratedOperations``: call any operation of the vendored OpenAPI specs by id."""

    async def call_operation(self, operation_id: str, *, path_params: Mapping[str, Any] | None = None, query: Mapping[str, Any] | None = None, body: Any = None,
                             headers: Mapping[str, str] | None = None) -> RawResponse:
        op = get(operation_id)
        if body is not None and not op.has_body:
            raise InvalidRequestError(f"{op.id} takes no request body", hint="Remove `body`.", operation=op.id)
        spec = RequestSpec(op.method, op.surface, op.render_path(path_params or {}), {k: v for k, v in (query or {}).items() if v is not None}, json=body, headers=headers or {},
                           idempotent=op.method in ("GET", "HEAD", "PUT", "DELETE") or op.access in ("read", "search"), operation=op.id)
        resp = await self.transport.send(spec)
        parsed: Any = None
        if resp.content and "json" in resp.headers.get("content-type", ""):
            parsed = resp.json()
        return RawResponse(status=resp.status_code, headers=dict(resp.headers), json_body=parsed, text=None if parsed is not None else resp.text)
