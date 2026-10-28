"""EventSource protocol and the queue-backed source used by every push receiver (design section 14.4)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import ClassVar, Protocol, runtime_checkable

import anyio

from ..model import ChangeEvent, EventSourceKind
from ..stores.base import LocalQueue


@runtime_checkable
class EventSource(Protocol):
    kind: ClassVar[EventSourceKind]
    method_ids: ClassVar[frozenset[str]]
    requires_public_ingress: ClassVar[bool]

    def events(self) -> AsyncIterator[ChangeEvent]: ...
    async def ack(self, event: ChangeEvent) -> None: ...
    async def nack(self, event: ChangeEvent, *, error: BaseException) -> None: ...
    async def aclose(self) -> None: ...


class LocalQueueSource:
    """Turns a durable ``LocalQueue`` (filled by webhook receivers) into an ``EventSource``."""

    kind: ClassVar[EventSourceKind] = EventSourceKind.JIRA_WEBHOOK
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-01", "DM-02", "DM-03", "DM-15", "DM-16"})
    requires_public_ingress: ClassVar[bool] = True

    def __init__(self, queue: LocalQueue, *, poll_interval: float = 0.05, kind: EventSourceKind | None = None) -> None:
        self.queue, self._interval, self._closed = queue, poll_interval, False
        self._ids: dict[str, str] = {}
        self.source_kind = kind or type(self).kind

    async def events(self) -> AsyncIterator[ChangeEvent]:
        while not self._closed:
            item = await self.queue.get()
            if item is None:
                await anyio.sleep(self._interval)
                continue
            qid, event = item
            self._ids[event.dedup_key] = qid
            yield event

    async def ack(self, event: ChangeEvent) -> None:
        qid = self._ids.pop(event.dedup_key, None)
        if qid is not None:
            await self.queue.ack(qid)

    async def nack(self, event: ChangeEvent, *, error: BaseException) -> None:
        # the item stays un-acked in the durable queue and is re-delivered after a restart; failures go to the DLQ in the pipeline
        self._ids.pop(event.dedup_key, None)

    async def aclose(self) -> None:
        self._closed = True
