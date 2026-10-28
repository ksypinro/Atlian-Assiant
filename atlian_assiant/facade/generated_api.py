"""``client.jira.api`` / ``client.confluence.api``: the generated REST tier (every OpenAPI operation) behind the normal policy."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..backends.rest_cloud import generated
from ..capabilities import BackendKind
from ..core.errors import NotFoundError
from ..models.webhooks import RawResponse
from ..policy import PlannedAction
from ..protocols import GeneratedOperations
from ..routing import Router
from ._namespace import Namespace
from .operations import OperationInfo

_List = list


class GeneratedApiClient(Namespace):
    """Every operation of the vendored OpenAPI specs for this product, callable by operationId (typed methods exist for the common ones; this covers the rest)."""

    def __init__(self, router: Router, apis: tuple[str, ...]) -> None:
        super().__init__(router)
        self._apis = apis

    def _resolve(self, operation: str) -> generated.GeneratedOperation:
        if operation.startswith(generated.PREFIX):
            op = generated.get(operation)
            if op.api not in self._apis:
                raise NotFoundError(f"{operation} belongs to another product", hint=f"This namespace serves: {', '.join(self._apis)}.")
            return op
        for api in self._apis:
            found = generated.load_index().get(f"{generated.PREFIX}{api}.{operation}")
            if found:
                return found
        raise NotFoundError(f"unknown operation {operation!r}", hint="List them with `operations(query)`.")

    async def operations(self, query: str | None = None, *, limit: int = 25) -> _List[OperationInfo]:
        """List the generated operations of this product, optionally filtered by a word.

        When to use:
            No typed method covers what you need: find the operationId, then `call` it.

        Args:
            query: Only operations whose id, summary or tags contain this text (case-insensitive).
            limit: Maximum results.

        Returns:
            Operation ids with their summary, access class, method, path and parameters.

        Raises:
            NotFoundError: Never; an empty list means nothing matched.

        Access: READ

        Example:
            >>> ops = await client.jira.api.operations("watchers")
        """
        needle = (query or "").lower()
        out = []
        for g in generated.load_index().values():
            if g.api not in self._apis or (needle and needle not in " ".join([g.id, g.summary, *g.tags]).lower()):
                continue
            out.append(OperationInfo(id=g.id, kind="generated", summary=g.summary, access=g.access.upper(), product=g.api, method=g.method, path=g.path,
                                     parameters=[f"{n} ({where}{', required' if req else ''})" for n, where, req in g.params] + (["body"] if g.has_body else [])))
        return out[: max(1, limit)]

    async def call(self, operation: str, *, path: Mapping[str, Any] | None = None, query: Mapping[str, Any] | None = None, body: Any = None, dry_run: bool = False,
                   backend: BackendKind | str | None = None) -> RawResponse | PlannedAction:
        """Call one generated operation by operationId under the normal access policy.

        When to use:
            After `operations` found it. Writes and deletes ask the user to confirm; preview them with dry_run.

        Args:
            operation: operationId ("getIssueWatchers") or the full id ("api.jira.getIssueWatchers").
            path: Path parameters, e.g. {"issueIdOrKey": "PROJ-1"}.
            query: Query parameters.
            body: JSON request body for operations that take one.
            dry_run: True returns the PlannedAction (what WOULD happen) without changing anything. Use it first for writes.
            backend: Force one backend. Default: the preset order.

        Returns:
            The raw response (status, headers, JSON body), or the PlannedAction for a dry run.

        Raises:
            NotFoundError: Unknown operation. InvalidRequestError: Missing path parameters or an unexpected body.
            PolicyDeniedError: The operation's access class is not enabled. ConfirmationDeclinedError: The user declined.

        Access: READ

        Example:
            >>> r = await client.jira.api.call("getIssueWatchers", path={"issueIdOrKey": "PROJ-1"})
        """
        g = self._resolve(operation)
        extra: dict[str, Any] = {"dry_run": True} if dry_run else {}
        result = await self._router.call(g.operation(), GeneratedOperations, "call_operation", g.id, path_params=path, query=query, body=body, backend=backend, **extra)
        return result  # type: ignore[no-any-return]
