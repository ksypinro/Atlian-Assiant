"""Access policy data model (design section 16.4). The PolicyGuard enforcing it is added in P1.AGENT.002."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from .capabilities import Access, BackendKind, Operation
from .core.errors import ConfirmationDeclinedError, PolicyDeniedError

if TYPE_CHECKING:
    from .config import AtlassianConfig


@dataclass(frozen=True)
class AccessPolicy:
    allowed: frozenset[Access] = frozenset({Access.READ, Access.SEARCH})
    confirm: frozenset[Access] = frozenset({Access.WRITE, Access.DELETE, Access.MANAGE})
    allow_credit_consuming: bool = False
    allowed_cloud_ids: frozenset[str] | None = None
    allowed_projects: frozenset[str] | None = None
    allowed_spaces: frozenset[str] | None = None
    max_results_per_call: int = 100
    max_attachment_bytes: int = 10 * 1024 * 1024
    allow_browser_writes: bool = False
    allow_direct_db: bool = False

    @classmethod
    def read_only(cls) -> AccessPolicy:
        return cls()

    @classmethod
    def read_write(cls) -> AccessPolicy:
        return cls(allowed=frozenset({Access.READ, Access.SEARCH, Access.WRITE}))

    @classmethod
    def analytics(cls) -> AccessPolicy:
        return cls(allowed=frozenset({Access.READ, Access.SEARCH, Access.ANALYTICS}))

    @classmethod
    def from_config(cls, config: AtlassianConfig) -> AccessPolicy:
        return cls(allowed=frozenset(config.access), allow_credit_consuming=config.allow_credit_consuming,
                   allow_browser_writes=config.allow_browser_writes, allow_direct_db=config.allow_direct_db)


@dataclass(frozen=True)
class PlannedAction:
    """What a write WOULD do. Returned by ``dry_run=True`` and shown to the confirmer."""

    operation: Operation
    target: str
    summary: str
    arguments: Mapping[str, Any]
    preferred_backend: BackendKind | None = None
    reversible: bool = True
    warnings: tuple[str, ...] = field(default_factory=tuple)


class ConfirmationHandler(Protocol):
    async def confirm(self, action: PlannedAction) -> bool: ...


# ----------------------------------------------------------------------------------------------------------------------
# Enforcement (design section 16.4)
# ----------------------------------------------------------------------------------------------------------------------
@dataclass
class GuardDecision:
    """What the guard decided: possibly rewritten arguments, or a dry-run plan to return instead of executing."""

    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    plan: PlannedAction | None = None


_ORDER_BY = re.compile(r"\border\s+by\b", re.IGNORECASE)
_JQL_ARG_OPS = {"jira.issue.search", "jira.issue.count"}
_JQL_KWARG_OPS = {"jira.agile.board.issues", "jira.agile.sprint.issues"}
_CQL_OPS = {"confluence.search.cql"}
_PROJECT_KEY_ARG_OPS = ("jira.issue.", "jira.project.")
#: jira.issue.* operations whose first argument is NOT an issue key (a JQL string, a draft, or an attachment id)
_NOT_ISSUE_KEY = {"jira.issue.search", "jira.issue.count", "jira.issue.create", "jira.issue.bulk_create"}
#: operations addressed by ids that carry no project, so project scope cannot be verified before the call
_ID_ONLY_JIRA = {"jira.issue.attachment.download", "jira.issue.attachment.delete"}
_SPACE_BY_ID_PREFIX = "confluence."
#: destructive access classes are ALWAYS confirmed, even if a policy lists nothing in ``confirm`` (design 17: destructive actions)
_ALWAYS_CONFIRM = frozenset({Access.DELETE, Access.MANAGE})
_ATTACHMENT_DOWNLOADS = {"jira.issue.attachment.download", "confluence.attachment.download"}

#: Atlassian MCP v2 permission-group scopes mirrored by ``Access`` (design 9-11). Request only what the policy allows.
MCP_SCOPES: dict[str, dict[Access, str]] = {
    "jira": {Access.READ: "read:jira:agent-interface", Access.SEARCH: "search:jira:agent-interface",
             Access.WRITE: "write:jira:agent-interface", Access.DELETE: "delete:jira:agent-interface",
             Access.MANAGE: "manage:jira:agent-interface"},
    "confluence": {Access.READ: "read:confluence:agent-interface", Access.SEARCH: "search:confluence:agent-interface",
                   Access.WRITE: "write:confluence:agent-interface"},
    "graph": {Access.READ: "read:all:twg", Access.WRITE: "write:all:twg"},
}
ROVO_SEARCH_SCOPE = "search:rovo:agent-interface"


def scopes_for(policy: AccessPolicy, *, include_rovo: bool | None = None) -> list[str]:
    """Minimal MCP OAuth scopes for a policy: nothing the policy does not allow is ever requested (least privilege)."""
    out: list[str] = []
    for groups in MCP_SCOPES.values():
        out += [scope for access, scope in groups.items() if access in policy.allowed]
    rovo = policy.allow_credit_consuming if include_rovo is None else include_rovo
    if rovo and Access.SEARCH in policy.allowed:
        out.append(ROVO_SEARCH_SCOPE)
    return sorted(set(out))


def _split_order(query: str, pattern: re.Pattern[str]) -> tuple[str, str]:
    m = pattern.search(query)
    if not m:
        return query.strip(), ""
    return query[:m.start()].strip(), " " + query[m.start():].strip()


def scope_jql(jql: str, projects: frozenset[str]) -> str:
    """Restrict a JQL query to the allowed projects: ``(<jql>) AND project in (A, B) ORDER BY ...``."""
    base, order = _split_order(jql, _ORDER_BY)
    clause = "project in (" + ", ".join(sorted(projects)) + ")"
    return (f"({base}) AND {clause}" if base else clause) + order


def scope_cql(cql: str, spaces: frozenset[str]) -> str:
    """Restrict a CQL query to the allowed spaces: ``(<cql>) AND space in ("A", "B") ORDER BY ...``."""
    base, order = _split_order(cql, _ORDER_BY)
    clause = "space in (" + ", ".join(f'"{s}"' for s in sorted(spaces)) + ")"
    return (f"({base}) AND {clause}" if base else clause) + order


class PolicyGuard:
    """Runs before routing.

    1. Is the access class allowed?  2. Were Rovo credits acknowledged?  3. Is the cloudId allowed?
    4. Project / space scope: keys are checked and JQL / CQL is rewritten.  5. Limits are clamped.
    6. Writes: build a PlannedAction; ``dry_run=True`` returns it, otherwise the ConfirmationHandler decides.
    """

    def __init__(self, policy: AccessPolicy, confirm: ConfirmationHandler | None = None, *, cloud_id: str | None = None) -> None:
        self.policy, self.confirm, self.cloud_id = policy, confirm, cloud_id

    async def authorize(self, op: Operation, args: tuple[Any, ...], kwargs: dict[str, Any]) -> GuardDecision:
        p = self.policy
        kwargs = dict(kwargs)
        if op.access not in p.allowed:
            raise PolicyDeniedError(f"{op.access.value} access is not enabled for this session ({op.id})", operation=op.id,
                                    hint=f"Ask the user to allow {op.access.name} access (AccessPolicy.allowed / ATLASSIAN_ACCESS).")
        if op.consumes_credits and not p.allow_credit_consuming:
            raise PolicyDeniedError(f"{op.id} consumes Rovo credits, which are not enabled", operation=op.id,
                                    hint="Set allow_credit_consuming=true (ATLASSIAN_ALLOW_CREDIT_CONSUMING) to permit it.")
        if p.allowed_cloud_ids is not None and self.cloud_id is not None and self.cloud_id not in p.allowed_cloud_ids:
            raise PolicyDeniedError(f"cloudId {self.cloud_id} is not allowed by the policy", operation=op.id,
                                    hint="The session is pinned to other Atlassian sites.")
        args = self._scope(op, args, kwargs)
        self._clamp(op, kwargs)
        dry_run = bool(kwargs.pop("dry_run", False))
        if op.access in (Access.WRITE, Access.DELETE, Access.MANAGE):
            plan = self._plan(op, args, kwargs)
            if dry_run:
                return GuardDecision(args, kwargs, plan)             # report what WOULD happen; send nothing
            if op.access in p.confirm or op.access in _ALWAYS_CONFIRM:
                if self.confirm is None:
                    raise ConfirmationDeclinedError(f"{op.id} needs confirmation but no ConfirmationHandler is configured", operation=op.id,
                                                    hint=f"Call with dry_run=True to preview, or configure a handler. Plan: {plan.summary}")
                if not await self.confirm.confirm(plan):
                    raise ConfirmationDeclinedError(f"the user declined: {plan.summary}", operation=op.id,
                                                    hint="Do not retry the same action; ask the user what they want instead.")
        return GuardDecision(args, kwargs, None)

    # ---- helpers ----
    def _scope(self, op: Operation, args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[Any, ...]:
        p = self.policy
        if p.allowed_projects is not None:
            if op.id in _JQL_ARG_OPS and args:
                args = (scope_jql(str(args[0]), p.allowed_projects), *args[1:])
            if op.id in _JQL_KWARG_OPS:
                kwargs["jql"] = scope_jql(str(kwargs.get("jql") or ""), p.allowed_projects)
            if op.id in _ID_ONLY_JIRA:
                raise PolicyDeniedError(f"{op.id} addresses an attachment by id, which cannot be limited to the allowed projects", operation=op.id,
                                        hint="List attachments through jira_get_issue on an allowed issue instead.")
            if op.id.startswith(_PROJECT_KEY_ARG_OPS) and op.id not in _NOT_ISSUE_KEY and args and isinstance(args[0], str):
                self._check_project(op, args[0])
        if p.allowed_spaces is not None:
            if op.id in _CQL_OPS and args:
                args = (scope_cql(str(args[0]), p.allowed_spaces), *args[1:])
            elif op.id.startswith(_SPACE_BY_ID_PREFIX) and op.id not in _CQL_OPS and op.id != "confluence.space.list":
                raise PolicyDeniedError(f"{op.id} reads content by id, which cannot be limited to the allowed spaces", operation=op.id,
                                        hint="Use confluence_search_cql (it is restricted to the allowed spaces), then read the hits.")
            if op.id == "confluence.space.list":
                allowed = sorted(p.allowed_spaces)
                requested = list(kwargs.get("keys") or ())
                kwargs["keys"] = [k for k in requested if k in p.allowed_spaces] or allowed
        return args

    def _check_project(self, op: Operation, key: str) -> None:
        assert self.policy.allowed_projects is not None
        project = key.split("-")[0].upper() if op.id.startswith("jira.issue.") else key.upper()
        if not (key.isdigit() is False and project in self.policy.allowed_projects):
            raise PolicyDeniedError(f"{key} is outside the allowed projects {sorted(self.policy.allowed_projects)}", operation=op.id,
                                    hint="Only issues of the allowed projects can be accessed; use their keys (not numeric ids).")

    def _clamp(self, op: Operation, kwargs: dict[str, Any]) -> None:
        p = self.policy
        for name in ("limit", "page_size"):
            if isinstance(kwargs.get(name), int) and kwargs[name] > p.max_results_per_call:
                kwargs[name] = p.max_results_per_call
        if op.id in _ATTACHMENT_DOWNLOADS:
            cur = kwargs.get("max_bytes")
            kwargs["max_bytes"] = p.max_attachment_bytes if cur is None else min(int(cur), p.max_attachment_bytes)

    def _plan(self, op: Operation, args: tuple[Any, ...], kwargs: Mapping[str, Any]) -> PlannedAction:
        target = str(args[0]) if args else op.id
        shown = {k: v for k, v in kwargs.items() if k != "backend"}
        return PlannedAction(operation=op, target=target, summary=f"{op.summary}: {target}", arguments={"args": list(args), **shown},
                             reversible=op.access is Access.WRITE and op.idempotent,
                             warnings=("destructive: cannot be undone",) if op.access is Access.DELETE else ())


class CliConfirmation:
    """Asks on the terminal."""

    async def confirm(self, action: PlannedAction) -> bool:
        return input(f"{action.summary}\n  warnings: {', '.join(action.warnings) or 'none'}\nProceed? [y/N] ").strip().lower() == "y"


class CallbackConfirmation:
    """Delegates to ``fn(plan) -> bool | Awaitable[bool]`` (e.g. a chat UI button)."""

    def __init__(self, fn: Any) -> None:
        self._fn = fn

    async def confirm(self, action: PlannedAction) -> bool:
        result = self._fn(action)
        if hasattr(result, "__await__"):
            result = await result
        return bool(result)


class DenyAll:
    async def confirm(self, action: PlannedAction) -> bool:
        return False


class AutoApprove:
    """Approves everything. For services only: refused when the client uses the agent preset."""

    async def confirm(self, action: PlannedAction) -> bool:
        return True
