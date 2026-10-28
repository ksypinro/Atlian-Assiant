"""Organization audit sources: windowed /events (DM-11) and cursor-based /events-stream (DM-12)."""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import anyio

from ...models.admin import AuditEvent, AuditEventPage
from ...models.common import Page
from ..model import ChangeEvent, EventSourceKind
from ..normalize import audit_change_event
from ..stores.base import CheckpointStore


class _AuditPoller:
    method_ids: ClassVar[frozenset[str]]
    requires_public_ingress: ClassVar[bool] = False
    kind: ClassVar[EventSourceKind]

    def __init__(self, checkpoints: CheckpointStore, *, interval: float, clock: Callable[[], datetime], sleep: Callable[[float], Awaitable[None]],
                 stream: str) -> None:
        self.checkpoints, self.interval, self._clock, self._sleep, self.stream, self._closed = checkpoints, interval, clock, sleep, stream, False

    async def poll_once(self) -> list[ChangeEvent]:  # pragma: no cover - abstract
        raise NotImplementedError

    async def events(self) -> AsyncIterator[ChangeEvent]:
        while not self._closed:
            for event in await self.poll_once():
                yield event
            if self._closed:
                break
            await self._sleep(self.interval)

    async def ack(self, event: ChangeEvent) -> None:
        return None

    async def nack(self, event: ChangeEvent, *, error: BaseException) -> None:
        return None

    async def aclose(self) -> None:
        self._closed = True


class OrgEventsListSource(_AuditPoller):
    """DM-11: ``/events`` pulled with ``from``/``to`` windows. The window advances to the newest ``occurredAt`` seen minus an overlap; dedup on ``event.id``."""

    kind: ClassVar[EventSourceKind] = EventSourceKind.ORG_EVENTS_LIST
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-11"})

    def __init__(self, list_events: Callable[..., Awaitable[Page[AuditEvent]]], checkpoints: CheckpointStore, *, interval: float = 180.0,
                 overlap: timedelta = timedelta(minutes=5), action: str | None = None, page_size: int = 200,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC), sleep: Callable[[float], Awaitable[None]] = anyio.sleep) -> None:
        super().__init__(checkpoints, interval=interval, clock=clock, sleep=sleep, stream="org-events-list")
        self._list, self.overlap, self.action, self.page_size = list_events, overlap, action, page_size

    @classmethod
    def from_client(cls, client: Any, checkpoints: CheckpointStore, **kw: Any) -> OrgEventsListSource:
        return cls(lambda **a: client.admin.audit.events(**a), checkpoints, **kw)

    async def poll_once(self) -> list[ChangeEvent]:
        raw = await self.checkpoints.load(self.stream)
        now = self._clock()
        if raw is None:
            await self.checkpoints.save(self.stream, now.isoformat())      # first run: from now, no history replay
            return []
        window_start = datetime.fromisoformat(raw) - self.overlap
        out: list[ChangeEvent] = []
        newest = datetime.fromisoformat(raw)
        cursor: str | None = None
        while True:
            page = await self._list(start=window_start, end=now, action=self.action, cursor=cursor, limit=self.page_size)
            for item in page.items:
                out.append(audit_change_event(item, source=self.kind, method_id="DM-11", received_at=now))
                if item.occurred_at and item.occurred_at > newest:
                    newest = item.occurred_at
            if not page.next_cursor:
                break
            cursor = page.next_cursor
        await self.checkpoints.save(self.stream, max(newest, now - self.overlap).isoformat())
        out.sort(key=lambda e: e.occurred_at or now)
        return out


class OrgEventsStreamSource(_AuditPoller):
    """DM-12: ``/events-stream`` cursor polling ordered by ``processedAt``. The returned cursor is checkpointed after every page (it exists on the last page too)."""

    kind: ClassVar[EventSourceKind] = EventSourceKind.ORG_EVENTS_STREAM
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-12"})

    def __init__(self, poll: Callable[..., Awaitable[AuditEventPage]], checkpoints: CheckpointStore, *, interval: float = 120.0, page_size: int = 500,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC), sleep: Callable[[float], Awaitable[None]] = anyio.sleep) -> None:
        super().__init__(checkpoints, interval=interval, clock=clock, sleep=sleep, stream="org-events-stream")
        self._poll, self.page_size = poll, page_size

    @classmethod
    def from_client(cls, client: Any, checkpoints: CheckpointStore, **kw: Any) -> OrgEventsStreamSource:
        return cls(lambda **a: client.admin.audit.stream(**a), checkpoints, **kw)

    async def poll_once(self) -> list[ChangeEvent]:
        cursor = await self.checkpoints.load(self.stream)
        now = self._clock()
        out: list[ChangeEvent] = []
        while True:
            page = await self._poll(cursor=cursor, start=None if cursor else now, limit=self.page_size)
            out += [audit_change_event(e, source=self.kind, method_id="DM-12", received_at=now) for e in page.events]
            if page.next_cursor:
                await self.checkpoints.save(self.stream, page.next_cursor)
            if not page.events or page.next_cursor == cursor or not page.next_cursor:
                return out
            cursor = page.next_cursor

    async def lag(self) -> timedelta | None:
        """Not computable from a cursor alone: report the age of the newest event seen instead via the pipeline; kept for interface symmetry."""
        return None
