"""Per-operation backend selection with safe fallback (design sections 12.3 and 12.4)."""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel

from .capabilities import BackendKind as B
from .capabilities import Operation
from .core.errors import (
    AtlassianError,
    BackendUnavailableError,
    DeploymentMismatchError,
    NoBackendAvailableError,
    UnsupportedOperationError,
)
from .core.pagination import Cursor
from .models.common import AtlassianModel, Page, SourceRef
from .protocols.base import Backend, implementation_of

if TYPE_CHECKING:
    from .policy import GuardDecision


class Preset(StrEnum):
    SERVICE = "service"          # deterministic services / ETL     (DA 12 "Prefer REST when")
    AGENT = "agent"              # LLM agents                        (DA 12 "Use MCP when")
    SHELL = "shell"              # humans / CI in a terminal         (DA 14-15)
    ANALYTICS = "analytics"      # BI / history                      (DA 21, 29)
    DATA_CENTER = "data_center"  # self-hosted                       (DA 23)


PRESET_ORDERS: Mapping[Preset, Mapping[str, Sequence[B]]] = {
    Preset.SERVICE: {
        "*": (B.REST, B.MCP, B.GRAPHQL, B.ACLI, B.TWG, B.JIRA_CLI, B.BROWSER),
        "graph.*": (B.MCP, B.GRAPHQL, B.TWG),
        "analytics.*": (B.DATA_LAKE,),
        "export.*": (B.REST, B.MCP, B.ACLI, B.BROWSER),
        "admin.*": (B.ORG_ADMIN,),
    },
    Preset.AGENT: {
        "*": (B.MCP, B.REST, B.TWG, B.GRAPHQL, B.ACLI, B.JIRA_CLI, B.BROWSER),
        "jira.jql.*": (B.REST,),                        # MCP has no JQL helper tools
        "confluence.content.delete": (B.REST,),         # MCP has no Confluence delete
        "graph.*": (B.MCP, B.TWG, B.GRAPHQL),
        "analytics.*": (B.DATA_LAKE,),
        "admin.*": (B.ORG_ADMIN,),
    },
    Preset.SHELL: {
        "*": (B.ACLI, B.TWG, B.JIRA_CLI, B.REST, B.MCP, B.BROWSER),
        "confluence.*": (B.TWG, B.REST, B.MCP, B.BROWSER),   # ACLI has no Confluence tree
    },
    Preset.ANALYTICS: {
        "*": (B.REST, B.MCP),
        "analytics.*": (B.DATA_LAKE,),
        "jira.issue.search": (B.REST,),
    },
    Preset.DATA_CENTER: {
        "*": (B.REST_DC, B.JIRA_CLI, B.BROWSER, B.DATABASE),
    },
}


class SelectionPolicy:
    """Ordered backend preference per operation: exact id -> longest wildcard prefix -> ``*``."""

    def __init__(self, orders: Mapping[str, Sequence[B]]) -> None:
        if "*" not in orders:
            raise ValueError("a selection policy needs a '*' default order")
        self._orders = dict(orders)

    @classmethod
    def from_preset(cls, preset: Preset, overrides: Mapping[str, Sequence[B]] | None = None) -> SelectionPolicy:
        return cls({**PRESET_ORDERS[preset], **(overrides or {})})

    def order_for(self, op: Operation) -> Sequence[B]:
        if op.id in self._orders:
            return self._orders[op.id]
        parts = op.id.split(".")
        for i in range(len(parts) - 1, 0, -1):
            key = ".".join(parts[:i]) + ".*"
            if key in self._orders:
                return self._orders[key]
        return self._orders["*"]


class CircuitBreaker:
    """Opens after ``threshold`` consecutive unavailable results; half-opens after ``cooldown`` seconds."""

    def __init__(self, threshold: int = 3, cooldown: float = 30.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.threshold, self.cooldown, self._clock = threshold, cooldown, clock
        self._failures: dict[B, int] = {}
        self._opened_at: dict[B, float] = {}

    def is_open(self, kind: B) -> bool:
        opened = self._opened_at.get(kind)
        if opened is None:
            return False
        if self._clock() - opened >= self.cooldown:
            return False                      # half-open: let one probe through
        return True

    def record_failure(self, kind: B) -> None:
        self._failures[kind] = self._failures.get(kind, 0) + 1
        if self._failures[kind] >= self.threshold:
            self._opened_at[kind] = self._clock()

    def record_success(self, kind: B) -> None:
        self._failures.pop(kind, None)
        self._opened_at.pop(kind, None)


class Guard(Protocol):
    """Runs before routing (the PolicyGuard of design section 16.4 implements this)."""

    async def authorize(self, op: Operation, args: tuple[Any, ...], kwargs: dict[str, Any]) -> GuardDecision: ...


class AllowAll:
    async def authorize(self, op: Operation, args: tuple[Any, ...], kwargs: dict[str, Any]) -> GuardDecision:
        from .policy import GuardDecision

        return GuardDecision(args, dict(kwargs))


def cursor_backend(cursor: str) -> B:
    return Cursor.decode(cursor).backend


def stamp_source(result: Any, kind: B, *, site: str | None = None, cloud_id: str | None = None) -> Any:
    """Record which backend served a result (``source.backend``) on models and pages."""
    ref = SourceRef(backend=kind, site=site, cloud_id=cloud_id, fetched_at=datetime.now(UTC))

    def stamp(obj: Any) -> None:
        if isinstance(obj, AtlassianModel) and obj.source is None:
            obj.source = ref

    if isinstance(result, Page):
        result.source = result.source or ref
        for item in result.items:
            stamp(item)
    elif isinstance(result, list):
        for item in result:
            stamp(item)
    elif isinstance(result, BaseModel):
        stamp(result)
    return result


def _explain(attempts: list[tuple[B, AtlassianError]], order: Sequence[B]) -> str:
    if not attempts:
        return ("No configured backend supports this operation. Candidates were: "
                + ", ".join(k.value for k in order) + ". Run client.describe() to see what is configured and why.")
    return "Tried: " + "; ".join(f"{k.value} ({e.message})" for k, e in attempts)


class Router:
    """Picks a backend per operation and falls back ONLY when it is safe.

    Fallback rules:
      1. UnsupportedOperationError / BackendUnavailableError -> try the next candidate. Backends MUST raise these only
         when NOTHING was sent to Atlassian.
      2. Domain answers (404, 403, 400, 409) are returned to the caller; they are facts, not outages.
      3. After a non-idempotent request was sent, any error propagates (no duplicate writes).
      4. A forced backend (``backend=``) disables fallback.
      5. A cursor pins the backend that produced it.
    """

    def __init__(self, backends: Mapping[B, Backend], selection: SelectionPolicy, guard: Guard | None = None,
                 breaker: CircuitBreaker | None = None, *, site: str | None = None, cloud_id: str | None = None,
                 deployment_cloud: bool = True) -> None:
        self._backends, self._selection = backends, selection
        self._guard: Guard = guard or AllowAll()
        self._breaker = breaker or CircuitBreaker()
        self.site, self.cloud_id, self._cloud = site, cloud_id, deployment_cloud
        self._default_backend: B | None = None

    def guarded(self, guard: Guard) -> Router:
        """A view of this router under a different guard (policy)."""
        clone = Router(self._backends, self._selection, guard, self._breaker, site=self.site, cloud_id=self.cloud_id,
                       deployment_cloud=self._cloud)
        clone._default_backend = self._default_backend
        return clone

    def forced(self, backend: B | str | None) -> Router:
        """A view of this router that sends every call to one backend (no fallback)."""
        clone = Router(self._backends, self._selection, self._guard, self._breaker, site=self.site, cloud_id=self.cloud_id,
                       deployment_cloud=self._cloud)
        clone._default_backend = B(backend) if backend else None
        return clone

    @property
    def backends(self) -> Mapping[B, Backend]:
        return self._backends

    def candidates(self, op: Operation, forced: B | None = None) -> list[Backend]:
        order = (forced,) if forced else self._selection.order_for(op)
        result: list[Backend] = []
        for kind in order:
            backend = self._backends.get(kind)
            if backend is None or op.id not in backend.supported_operations():
                continue
            if forced is None and self._breaker.is_open(kind):
                continue
            result.append(backend)
        return result

    async def call(self, op: Operation, protocol: type, method: str, /, *args: Any, backend: B | str | None = None,
                   cursor: str | None = None, **kwargs: Any) -> Any:
        if op.cloud_only and not self._cloud:
            raise DeploymentMismatchError(f"{op.id} is only available on Atlassian Cloud",
                                          hint="This operation is Cloud-only; use a Data Center equivalent or REST.",
                                          operation=op.id)
        forced = B(backend) if backend else (cursor_backend(cursor) if cursor else self._default_backend)
        decision = await self._guard.authorize(op, args, kwargs)
        if decision.plan is not None:
            return decision.plan                     # dry run: report what WOULD happen, execute nothing
        args, kwargs = decision.args, decision.kwargs
        attempts: list[tuple[B, AtlassianError]] = []
        for impl in self.candidates(op, forced):
            target = implementation_of(impl, protocol)
            if target is None:
                continue
            call_kwargs = {**kwargs, "cursor": cursor} if cursor is not None else kwargs
            try:
                result = await getattr(target, method)(*args, **call_kwargs)
            except (UnsupportedOperationError, BackendUnavailableError) as exc:
                attempts.append((impl.kind, exc))
                self._breaker.record_failure(impl.kind)
                if forced:
                    break
                continue
            self._breaker.record_success(impl.kind)
            return stamp_source(result, impl.kind, site=self.site, cloud_id=self.cloud_id)
        raise NoBackendAvailableError(f"No backend could perform {op.id}", operation=op.id,
                                      hint=_explain(attempts, self._selection.order_for(op)),
                                      details={"attempts": [(k.value, str(e)) for k, e in attempts]})
