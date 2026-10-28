"""EventPipeline (design section 14.12): merge sources -> dedup -> filters -> enrich -> version gate -> handlers -> sinks."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import anyio

from ..capabilities import Product
from .model import ChangeAction, ChangeEvent, EntityType
from .sources.base import EventSource
from .stores.base import DeadLetterSink, DedupStore

if TYPE_CHECKING:
    from .enrich import CanonicalEnricher

Handler = Callable[[ChangeEvent], Awaitable[None]]
Filter = Callable[[ChangeEvent], bool]
Selector = tuple["Product | None", "EntityType | None", "ChangeAction | None"]


class EventSink(Protocol):
    async def publish(self, event: ChangeEvent) -> None: ...


@dataclass
class PipelineStats:
    received: int = 0
    duplicates: int = 0
    filtered: int = 0
    stale: int = 0
    handled: int = 0
    retried: int = 0
    dead_lettered: int = 0
    sink_errors: int = 0
    handler_calls: int = 0
    dlq_reasons: list[str] = field(default_factory=list)


class EventPipeline:
    """Consumes every source in its own task and runs handlers with dedup, retry/backoff and a dead-letter sink.

    An event is marked *seen* only after all handlers succeed, so a crash re-delivers it (at-least-once); the dedup key makes
    that safe. Handlers should be idempotent.
    """

    def __init__(self, sources: Sequence[EventSource], *, dedup: DedupStore, enricher: CanonicalEnricher | None = None,
                 sinks: Sequence[EventSink] = (), concurrency: int = 8, max_attempts: int = 5,
                 dead_letter: DeadLetterSink | None = None, backoff_base: float = 0.5, backoff_max: float = 30.0) -> None:
        self.sources, self.dedup, self.enricher, self.sinks = list(sources), dedup, enricher, list(sinks)
        self.dead_letter, self.max_attempts = dead_letter, max_attempts
        self._concurrency, self._backoff_base, self._backoff_max = concurrency, backoff_base, backoff_max
        self._handlers: list[tuple[Selector, Handler]] = []
        self._filters: list[Filter] = []
        self.stats = PipelineStats()

    def on(self, product: Product | None = None, entity: EntityType | None = None,
           action: ChangeAction | None = None) -> Callable[[Handler], Handler]:
        """Register a handler: ``@pipeline.on(Product.JIRA, EntityType.ISSUE, ChangeAction.UPDATED)``. ``None`` matches anything."""
        def register(fn: Handler) -> Handler:
            self._handlers.append(((product, entity, action), fn))
            return fn
        return register

    def filter(self, fn: Filter) -> Filter:
        """Register a filter; events for which any filter returns False are acknowledged and skipped."""
        self._filters.append(fn)
        return fn

    def _matching(self, event: ChangeEvent) -> list[Handler]:
        return [h for (p, e, a), h in self._handlers
                if (p is None or p == event.product) and (e is None or e == event.entity_type) and (a is None or a == event.action)]

    async def run(self, *, stop: anyio.Event | None = None) -> None:
        """Run until ``stop`` is set or every source is exhausted."""
        limiter = anyio.Semaphore(self._concurrency)
        async with anyio.create_task_group() as tg:
            for source in self.sources:
                tg.start_soon(self._consume, source, limiter, stop)
            if stop is not None:
                async def _watch() -> None:
                    await stop.wait()
                    tg.cancel_scope.cancel()
                tg.start_soon(_watch)

    async def _consume(self, source: EventSource, limiter: anyio.Semaphore, stop: anyio.Event | None) -> None:
        async with anyio.create_task_group() as tg:
            async for event in source.events():
                if stop is not None and stop.is_set():
                    break
                await limiter.acquire()
                tg.start_soon(self._guarded, source, event, limiter)

    async def _guarded(self, source: EventSource, event: ChangeEvent, limiter: anyio.Semaphore) -> None:
        try:
            await self.process(event, source)
        finally:
            limiter.release()

    async def process(self, event: ChangeEvent, source: EventSource | None = None) -> bool:
        """Run one event through the pipeline. Returns True when handlers ran successfully."""
        self.stats.received += 1
        if await self.dedup.seen(event.dedup_key):
            self.stats.duplicates += 1
            await self._ack(source, event)
            return False
        if any(not f(event) for f in self._filters):
            self.stats.filtered += 1
            await self.dedup.mark(event.dedup_key)
            await self._ack(source, event)
            return False
        if self.enricher is not None:
            try:
                enriched = await self.enricher.enrich(event)
            except Exception as exc:                          # fresh read failed (auth, 5xx): retry later, never lose the event
                await self._fail(source, event, exc)
                return False
            if enriched is None:
                self.stats.stale += 1
                await self.dedup.mark(event.dedup_key)
                await self._ack(source, event)
                return False
            event = enriched
        for handler in self._matching(event):
            error = await self._run_handler(handler, event)
            if error is not None:
                await self._fail(source, event, error)
                return False
        await self.dedup.mark(event.dedup_key)
        await self._ack(source, event)
        self.stats.handled += 1
        for sink in self.sinks:
            try:
                await sink.publish(event)
            except Exception:                                 # a sink outage must not re-run handlers
                self.stats.sink_errors += 1
        return True

    async def _run_handler(self, handler: Handler, event: ChangeEvent) -> BaseException | None:
        last: BaseException | None = None
        for attempt in range(1, self.max_attempts + 1):
            self.stats.handler_calls += 1
            try:
                await handler(event)
                return None
            except Exception as exc:
                last = exc
                if attempt < self.max_attempts:
                    self.stats.retried += 1
                    await anyio.sleep(min(self._backoff_max, self._backoff_base * 2 ** (attempt - 1)))
        return last

    async def _fail(self, source: EventSource | None, event: ChangeEvent, error: BaseException) -> None:
        self.stats.dead_lettered += 1
        self.stats.dlq_reasons.append(f"{type(error).__name__}: {error}")
        if self.dead_letter is not None:
            await self.dead_letter.put(event, error)
        if source is not None:
            await source.nack(event, error=error)

    @staticmethod
    async def _ack(source: EventSource | None, event: ChangeEvent) -> None:
        if source is not None:
            await source.ack(event)
