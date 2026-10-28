"""Reconciler (DM-38): periodic windowed sweep + id-set diff, the correctness path that finds deletes polling cannot see."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

import anyio

from ...capabilities import Product
from ..model import ChangeAction, ChangeEvent, DeliveryInfo, EntityType, EventSourceKind
from ..stores.base import CheckpointStore

Snapshot = Callable[[], Awaitable[Mapping[str, str]]]


@dataclass(frozen=True)
class ReconcileResult:
    compared: int
    created: int
    changed: int
    deleted: int


def _entity(ref: str) -> tuple[EntityType, str]:
    kind, _, ident = ref.partition(":")
    try:
        return EntityType(kind), ident
    except ValueError:
        return EntityType.UNKNOWN, ident


class ReconcilerSource:
    """Compares a full snapshot (``entity_ref -> version``, e.g. ``issue:PROJ-1 -> updated``) with the last one and emits RECONCILER events.

    * present now, absent before   -> CREATED
    * present in both, version differs -> UPDATED
    * absent now, present before   -> DELETED (the only way polling-only setups learn about deletes)
    The event id is ``ref:version`` (``ref:deleted`` for deletes), so repeated sweeps and webhook duplicates collapse in dedup.
    """

    kind: ClassVar[EventSourceKind] = EventSourceKind.RECONCILER
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-38"})
    requires_public_ingress: ClassVar[bool] = False

    def __init__(self, snapshot: Snapshot, checkpoints: CheckpointStore, *, name: str = "default", interval: float = 86400.0,
                 product: Product = Product.JIRA, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 sleep: Callable[[float], Awaitable[None]] = anyio.sleep) -> None:
        self._snapshot, self.checkpoints, self.stream, self.interval = snapshot, checkpoints, f"reconcile:{name}", interval
        self.product, self._clock, self._sleep, self._closed = product, clock, sleep, False
        self._pending: list[ChangeEvent] = []

    @classmethod
    def for_jql(cls, client: Any, jql: str, checkpoints: CheckpointStore, **kwargs: Any) -> ReconcilerSource:
        async def snapshot() -> Mapping[str, str]:
            out: dict[str, str] = {}
            cursor: str | None = None
            while True:
                page = await client.jira.issues.search(jql, fields=["updated"], limit=100, cursor=cursor)
                out.update({f"issue:{i.key}": i.updated.isoformat() if i.updated else "" for i in page.items})
                if not page.next_cursor:
                    return out
                cursor = page.next_cursor
        return cls(snapshot, checkpoints, name=kwargs.pop("name", "jql"), **kwargs)

    @classmethod
    def for_cql(cls, client: Any, cql: str, checkpoints: CheckpointStore, **kwargs: Any) -> ReconcilerSource:
        async def snapshot() -> Mapping[str, str]:
            out: dict[str, str] = {}
            cursor: str | None = None
            while True:
                page = await client.confluence.search.cql(cql, limit=100, cursor=cursor, excerpt=False)
                out.update({f"page:{h.content_id}": h.last_modified.isoformat() if h.last_modified else "" for h in page.items if h.content_id})
                if not page.next_cursor:
                    return out
                cursor = page.next_cursor
        return cls(snapshot, checkpoints, name=kwargs.pop("name", "cql"), product=Product.CONFLUENCE, **kwargs)

    def _make(self, ref: str, version: str, action: ChangeAction) -> ChangeEvent:
        entity, ident = _entity(ref)
        is_issue = entity is EntityType.ISSUE
        return ChangeEvent(event_id=f"{ref}:{version or 'unknown'}", source=self.kind, product=self.product, entity_type=entity, action=action,
                           native_type=f"reconcile:{action.value}", entity_id=None if is_issue else ident, entity_key=ident if is_issue else None,
                           entity_version=None if action is ChangeAction.DELETED else version or None, received_at=self._clock(),
                           delivery=DeliveryInfo(method_id="DM-38", verified=True))

    async def sweep(self) -> ReconcileResult:
        """One reconciliation: snapshot, diff against the stored snapshot, queue events, store the new snapshot."""
        current = dict(await self._snapshot())
        raw = await self.checkpoints.load(self.stream)
        previous: dict[str, str] = json.loads(raw) if raw else {}
        first = raw is None
        created = changed = deleted = 0
        if not first:                                   # the first sweep only establishes the baseline
            for ref, version in current.items():
                if ref not in previous:
                    created += 1
                    self._pending.append(self._make(ref, version, ChangeAction.CREATED))
                elif previous[ref] != version:
                    changed += 1
                    self._pending.append(self._make(ref, version, ChangeAction.UPDATED))
            for ref in previous.keys() - current.keys():
                deleted += 1
                self._pending.append(self._make(ref, "deleted", ChangeAction.DELETED))
        await self.checkpoints.save(self.stream, json.dumps(current, sort_keys=True))
        return ReconcileResult(len(current), created, changed, deleted)

    async def events(self) -> AsyncIterator[ChangeEvent]:
        while not self._closed:
            await self.sweep()
            while self._pending:
                yield self._pending.pop(0)
            if self._closed:
                break
            await self._sleep(self.interval)

    async def ack(self, event: ChangeEvent) -> None:
        return None

    async def nack(self, event: ChangeEvent, *, error: BaseException) -> None:
        return None

    async def aclose(self) -> None:
        self._closed = True


Reconciler = ReconcilerSource
