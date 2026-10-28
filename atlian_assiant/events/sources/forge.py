"""ForgeInboxSource (DM-07 / DM-08): pull the companion app's Storage inbox through the Forge App REST API, ack, checkpoint."""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, ClassVar

import anyio

from ..model import ChangeEvent, EventSourceKind
from ..normalize import forge_change_event
from ..stores.base import CheckpointStore


class ForgeInboxSource:
    """``GET /events?cursor=`` -> ChangeEvents (dedup key = inbox id). Acknowledgement happens only after the pipeline succeeded (``ack``), in batches;
    the cursor is checkpointed so a restart resumes where it stopped. Unacked records are re-delivered by the inbox (at-least-once)."""

    kind: ClassVar[EventSourceKind] = EventSourceKind.FORGE_INBOX
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-07", "DM-08"})
    requires_public_ingress: ClassVar[bool] = False

    def __init__(self, fetch: Callable[..., Awaitable[dict[str, Any]]], ack: Callable[[list[str]], Awaitable[None]], checkpoints: CheckpointStore, *,
                 interval: float = 30.0, page_size: int = 100, stream: str = "forge-inbox", clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 sleep: Callable[[float], Awaitable[None]] = anyio.sleep) -> None:
        self._fetch, self._ack, self.checkpoints, self.interval, self.page_size, self.stream = fetch, ack, checkpoints, interval, page_size, stream
        self._clock, self._sleep, self._closed = clock, sleep, False
        self._keys: dict[str, str] = {}
        self.deleted_pages: list[str] = []          # ids from the app's `del#` markers: the enricher invalidates descendants (EV section 11)

    @classmethod
    def from_backend(cls, backend: Any, checkpoints: CheckpointStore, **kw: Any) -> ForgeInboxSource:
        return cls(lambda **a: backend.app_rest.events(**a), lambda keys: backend.app_rest.ack(keys), checkpoints, **kw)

    async def poll_once(self) -> list[ChangeEvent]:
        cursor = await self.checkpoints.load(self.stream) or None
        out: list[ChangeEvent] = []
        while True:
            page = await self._fetch(cursor=cursor, limit=self.page_size)
            for rec in page.get("events", []):
                if rec.get("processed"):
                    continue
                ev = forge_change_event(rec, source=self.kind, method_id="DM-07", received_at=self._clock())
                self._keys[ev.event_id] = str(rec.get("key") or rec.get("id"))
                if ev.native_type.endswith(":deleted:page") and ev.entity_id:
                    self.deleted_pages.append(ev.entity_id)
                out.append(ev)
            nxt = page.get("nextCursor")
            if not nxt or nxt == cursor:
                return out
            cursor = nxt
            await self.checkpoints.save(self.stream, nxt)

    async def events(self) -> AsyncIterator[ChangeEvent]:
        while not self._closed:
            for ev in await self.poll_once():
                yield ev
            if self._closed:
                break
            await self._sleep(self.interval)

    async def ack(self, event: ChangeEvent) -> None:
        key = self._keys.pop(event.event_id, None)
        if key:
            await self._ack([key])

    async def nack(self, event: ChangeEvent, *, error: BaseException) -> None:
        self._keys.pop(event.event_id, None)        # not acked: the inbox keeps it unprocessed and it is pulled again

    async def aclose(self) -> None:
        self._closed = True
