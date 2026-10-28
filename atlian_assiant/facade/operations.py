"""The operation index as two meta-tools (design 16, P6): ``discover`` any operation by intent, ``call`` it by id.

Curated operations call the typed facade method (so every curated rule applies); generated OpenAPI operations go through the router with the access class derived
from their HTTP method. Either way the access policy, confirmation and ``dry_run`` rules are exactly those of the dedicated tools.
"""
from __future__ import annotations

import inspect
import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field

from .. import operations as ops
from ..backends.rest_cloud import generated
from ..core.errors import InvalidRequestError, NotFoundError
from ..models.common import AtlassianModel
from ..protocols import GeneratedOperations
from ._namespace import Namespace

_WORD = re.compile(r"[a-z0-9]+")


class OperationInfo(AtlassianModel):
    id: str
    kind: Literal["curated", "generated"]
    summary: str
    access: str
    product: str
    allowed: bool = True                                  # does the session's access policy allow it right now
    call: str = ""                                        # how to call it with `atlassian_call_operation`
    method: str | None = None
    path: str | None = None
    parameters: list[str] = Field(default_factory=list)


def _tokens(*texts: str) -> set[str]:
    out: set[str] = set()
    for t in texts:
        spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", t)                      # getIssueWatchers -> get Issue Watchers
        out |= set(_WORD.findall(spaced.lower().replace("_", " ").replace(".", " ")))
    return out


class OperationsClient(Namespace):
    """Search and call the full operation index: the curated catalog plus every generated OpenAPI operation."""

    __namespace__ = False

    def __init__(self, client: Any) -> None:
        super().__init__(client.router)
        self._client = client

    # ------------------------------------------------------------------------------------------------ index
    def _curated_methods(self) -> dict[str, tuple[Any, str]]:
        from ..agent.describe import walk_namespaces

        out: dict[str, tuple[Any, str]] = {}
        for _, ns in walk_namespaces(self._client):
            for op_id, method in getattr(ns, "_OPS", {}).items():
                out[op_id] = (ns, method)
        return out

    def _infos(self) -> list[OperationInfo]:
        allowed = self._client.policy.allowed
        curated = self._curated_methods()
        out = [OperationInfo(id=o.id, kind="curated", summary=o.summary, access=o.access.name, product=o.product.value, allowed=o.access in allowed,
                             call=f'arguments = the typed method\'s keyword arguments (see client.help("{o.id}"))' if o.id in curated else "no typed method yet: use raw.request")
               for o in ops.CATALOG.values()]
        out += [OperationInfo(id=g.id, kind="generated", summary=g.summary, access=g.access.upper(), product=g.api, allowed=g.operation().access in allowed, method=g.method, path=g.path,
                              parameters=[f"{n} ({where}{', required' if req else ''})" for n, where, req in g.params] + (["body"] if g.has_body else []),
                              call='arguments = {"path": {...}, "query": {...}, "body": {...}}') for g in generated.load_index().values()]
        return out

    async def discover(self, query: str, *, product: str | None = None, access: str | None = None, only_allowed: bool = False, limit: int = 10) -> list[OperationInfo]:
        """Search the full operation index (curated + generated OpenAPI) by intent.

        When to use:
            The dedicated tools do not cover what you need ("list issue watchers", "bulk get groups"). Search first, then call the operation by id.

        Args:
            query: What you want to do, in words ("votes on an issue", "blog posts").
            product: Only this product or API (jira, agile, jsm, confluence, confluence_v1, confluence_v2, ...).
            access: Only this access class (READ, SEARCH, WRITE, DELETE, MANAGE).
            only_allowed: Hide operations the current access policy would refuse.
            limit: Maximum results.

        Returns:
            Matching operations, best first, with their id, summary, access class, whether the policy allows them and how to call them.

        Raises:
            InvalidRequestError: The query is empty.

        Access: READ

        Example:
            >>> hits = await client.index.discover("issue watchers")
        """
        want = _tokens(query)
        if not want:
            raise InvalidRequestError("discover needs a query", hint="Describe what you want to do in a few words.")
        scored = []
        for info in self._infos():
            if product and product.lower() not in (info.product.lower(), info.product.lower().split("_")[0], info.id.split(".")[1] if info.id.startswith("api.") else ""):
                continue
            if access and info.access.upper() != access.upper():
                continue
            if only_allowed and not info.allowed:
                continue
            id_tokens, text_tokens = _tokens(info.id), _tokens(info.summary, *(info.parameters or []))
            score = 3 * len(want & id_tokens) + len(want & text_tokens)
            if score:
                scored.append((-score, info.kind != "curated", info.id, info))
        return [i for *_, i in sorted(scored)[: max(1, limit)]]

    async def call(self, operation: str, arguments: Mapping[str, Any] | None = None, *, dry_run: bool = False) -> Any:
        """Call any operation of the index by id, under the same policy and confirmation rules as the dedicated tools.

        When to use:
            After `discover` found the operation you need. Writes and deletes ask the user to confirm; preview them first with dry_run.

        Args:
            operation: Operation id from `discover` ("jira.issue.get" or "api.jira.getIssueWatchers").
            arguments: For curated operations: the typed method's keyword arguments. For generated ones: {"path": {...}, "query": {...}, "body": {...}}.
            dry_run: True returns the PlannedAction (what WOULD happen) without changing anything. Use it first for writes, show the user, then call again.

        Returns:
            What the dedicated method returns (a model, a page, ...), or the raw response for generated operations.

        Raises:
            NotFoundError: Unknown operation id.
            InvalidRequestError: Missing or unknown arguments.
            PolicyDeniedError: The access class of the operation is not enabled. ConfirmationDeclinedError: The user declined.

        Access: READ

        Example:
            >>> r = await client.index.call("jira.issue.get", {"key": "PROJ-1"})
        """
        args = dict(arguments or {})
        if operation.startswith(generated.PREFIX):
            g = generated.get(operation)
            unknown = set(args) - {"path", "query", "body", "headers"}
            if unknown:
                raise InvalidRequestError(f"unknown argument(s) {', '.join(sorted(unknown))} for a generated operation", hint='Use {"path": {...}, "query": {...}, "body": {...}}.',
                                          operation=operation)
            extra: dict[str, Any] = {"dry_run": True} if dry_run else {}
            return await self._router.call(g.operation(), GeneratedOperations, "call_operation", g.id, path_params=args.get("path"), query=args.get("query"), body=args.get("body"),
                                           headers=args.get("headers"), **extra)
        methods = self._curated_methods()
        if operation not in methods:
            if operation in ops.CATALOG:
                raise InvalidRequestError(f"{operation} has no typed method to call generically", hint="Use the raw namespace (raw.request) for it.", operation=operation)
            near = [k for k in list(ops.CATALOG) + list(generated.load_index()) if operation.split(".")[-1].lower() in k.lower()][:5]
            raise NotFoundError(f"unknown operation {operation!r}", hint=f"Search with discover. Similar: {near}")
        ns, name = methods[operation]
        fn = getattr(ns, name)
        params = inspect.signature(fn).parameters
        bad = [k for k in args if k not in params]
        if bad:
            raise InvalidRequestError(f"{operation} has no argument(s) {', '.join(bad)}", hint="Arguments: " + ", ".join(p for p in params if p not in ("backend", "dry_run")), operation=operation)
        if dry_run:
            if "dry_run" not in params:
                raise InvalidRequestError(f"{operation} is a read: dry_run only applies to writes", hint="Call it without dry_run.", operation=operation)
            args["dry_run"] = True
        return await fn(**args)
