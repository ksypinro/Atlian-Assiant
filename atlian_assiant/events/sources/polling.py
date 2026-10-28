"""Pollers (design 14.14, Template Method): checkpoint -> query (checkpoint - overlap) -> page -> emit -> advance -> sleep."""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anyio

from ...capabilities import BackendKind, Product
from ...core.errors import ConfigurationError
from ...models.common import Page
from ..model import ChangeEvent, EntityType, EventSourceKind
from ..normalize import poll_hit_event
from ..stores.base import CheckpointStore

DEFAULT_OVERLAP = timedelta(minutes=5)
FIELDS = ("updated", "created", "project", "summary")


@dataclass
class PollBatch:
    events: list[ChangeEvent]
    next_checkpoint: datetime | None


def parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def format_minute(moment: datetime, tz: ZoneInfo) -> str:
    """JQL/CQL datetimes have MINUTE precision and are evaluated in the API user's time zone: format accordingly."""
    return moment.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except ZoneInfoNotFoundError as exc:
        raise ConfigurationError(f"unknown time zone {name!r}", hint="Use an IANA name such as Europe/Berlin (the `timeZone` of /myself).") from exc


class PollingSource(ABC):
    """Shared loop. Subclasses implement ``query`` (one sweep from a checkpoint) and set ``stream`` / ``method_id``."""

    kind: ClassVar[EventSourceKind]
    method_ids: ClassVar[frozenset[str]]
    requires_public_ingress: ClassVar[bool] = False
    stream: str
    method_id: str

    def __init__(self, checkpoints: CheckpointStore, *, interval: float = 120.0, overlap: timedelta = DEFAULT_OVERLAP,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC), sleep: Callable[[float], Awaitable[None]] = anyio.sleep) -> None:
        self.checkpoints, self.interval, self.overlap, self._clock, self._sleep = checkpoints, interval, overlap, clock, sleep
        self._closed = False

    @abstractmethod
    def query(self, since: datetime) -> AsyncIterator[PollBatch]:
        """Yield pages of events for everything modified at or after ``since`` (already minus the overlap), oldest first."""

    async def poll_once(self) -> list[ChangeEvent]:
        """One sweep. The checkpoint advances after each page; the very first run starts from now (no history replay)."""
        stored = parse_dt(await self.checkpoints.load(self.stream))
        if stored is None:
            await self.checkpoints.save(self.stream, self._clock().isoformat())
            return []
        out: list[ChangeEvent] = []
        async for batch in self.query(stored - self.overlap):
            out += batch.events
            if batch.next_checkpoint is not None and batch.next_checkpoint > stored:
                stored = batch.next_checkpoint
                await self.checkpoints.save(self.stream, stored.isoformat())
        return out

    async def lag(self) -> timedelta | None:
        """How far behind the poller is: now minus its checkpoint (None before the first sweep). Alert when it exceeds a few intervals."""
        stored = parse_dt(await self.checkpoints.load(self.stream))
        return None if stored is None else self._clock() - stored

    async def events(self) -> AsyncIterator[ChangeEvent]:
        while not self._closed:
            for event in await self.poll_once():
                yield event
            if self._closed:
                break
            await self._sleep(self.interval)

    async def ack(self, event: ChangeEvent) -> None:        # the checkpoint is source-side; dedup absorbs overlap duplicates
        return None

    async def nack(self, event: ChangeEvent, *, error: BaseException) -> None:
        return None                                         # the overlap window re-reads it on the next sweep

    async def aclose(self) -> None:
        self._closed = True


SearchFn = Callable[..., Awaitable[Page[Any]]]


def _stream_name(prefix: str, query: str) -> str:
    return f"{prefix}:{hashlib.sha1(query.encode()).hexdigest()[:10]}"


class JqlPollingSource(PollingSource):
    """DM-36 (REST / DC / ACLI / TWG transports) and DM-48 (``transport=BackendKind.MCP``): JQL polling with overlap and dedup on ``id:updated``.

    Deletes are invisible to polling: always run the reconciler too (DM-38).
    """

    kind: ClassVar[EventSourceKind] = EventSourceKind.JQL_POLL
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-36", "DM-48"})

    def __init__(self, search: SearchFn, base_jql: str, checkpoints: CheckpointStore, *, timezone: str | None = None,
                 transport: BackendKind = BackendKind.REST, page_size: int = 100, **kwargs: Any) -> None:
        super().__init__(checkpoints, **kwargs)
        self._search, self.base_jql, self.tz, self.transport, self.page_size = search, base_jql, zone(timezone), transport, page_size
        self.stream = _stream_name("jql", base_jql)
        self.method_id = "DM-48" if transport is BackendKind.MCP else "DM-36"

    @classmethod
    async def from_client(cls, client: Any, base_jql: str, checkpoints: CheckpointStore, *, transport: BackendKind = BackendKind.REST,
                          **kwargs: Any) -> JqlPollingSource:
        """Build from a client: the checkpoint is formatted in the API user's own time zone (read from ``/myself``)."""
        tz = kwargs.pop("timezone", None)
        if tz is None:
            try:
                me = await client.raw.request("GET", "/myself", product="jira")
                tz = (me.json_body or {}).get("timeZone")
            except Exception:
                tz = None

        async def search(jql: str, *, limit: int, cursor: str | None) -> Page[Any]:
            page: Page[Any] = await client.jira.issues.search(jql, fields=list(FIELDS), limit=limit, cursor=cursor, backend=transport)
            return page
        return cls(search, base_jql, checkpoints, timezone=tz, transport=transport, **kwargs)

    def build_jql(self, since: datetime) -> str:
        return f'({self.base_jql}) AND updated >= "{format_minute(since, self.tz)}" ORDER BY updated ASC, key ASC'

    async def query(self, since: datetime) -> AsyncIterator[PollBatch]:
        jql, cursor = self.build_jql(since), None
        while True:
            page = await self._search(jql, limit=self.page_size, cursor=cursor)
            events, newest = [], None
            for issue in page.items:
                if issue.updated is None:
                    continue
                newest = max(newest, issue.updated) if newest else issue.updated
                events.append(poll_hit_event(
                    source=self.kind, product=Product.JIRA, entity=EntityType.ISSUE, entity_id=issue.id, version=issue.updated.isoformat(),
                    key=issue.key, container=issue.project.key if issue.project else None,
                    created=bool(issue.created and abs((issue.updated - issue.created).total_seconds()) < 2),
                    method_id=self.method_id, backend=self.transport, received_at=self._clock()))
            yield PollBatch(events, newest)
            if not page.next_cursor:
                return
            cursor = page.next_cursor


class CqlPollingSource(PollingSource):
    """DM-37 (REST/DC) and DM-48 (``transport=BackendKind.MCP``): CQL ``lastmodified`` polling returning ids; hydration happens in the enricher."""

    kind: ClassVar[EventSourceKind] = EventSourceKind.CQL_POLL
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-37", "DM-48"})

    def __init__(self, search: SearchFn, base_cql: str, checkpoints: CheckpointStore, *, timezone: str | None = None,
                 transport: BackendKind = BackendKind.REST, page_size: int = 100, **kwargs: Any) -> None:
        super().__init__(checkpoints, **kwargs)
        self._search, self.base_cql, self.tz, self.transport, self.page_size = search, base_cql, zone(timezone), transport, page_size
        self.stream = _stream_name("cql", base_cql)
        self.method_id = "DM-48" if transport is BackendKind.MCP else "DM-37"

    @classmethod
    def from_client(cls, client: Any, base_cql: str, checkpoints: CheckpointStore, *, transport: BackendKind = BackendKind.REST,
                    **kwargs: Any) -> CqlPollingSource:
        async def search(cql: str, *, limit: int, cursor: str | None) -> Page[Any]:
            page: Page[Any] = await client.confluence.search.cql(cql, limit=limit, cursor=cursor, excerpt=False, backend=transport)
            return page
        return cls(search, base_cql, checkpoints, transport=transport, **kwargs)

    def build_cql(self, since: datetime) -> str:
        return f'({self.base_cql}) AND lastmodified >= "{format_minute(since, self.tz)}" ORDER BY lastmodified ASC'

    async def query(self, since: datetime) -> AsyncIterator[PollBatch]:
        cql, cursor = self.build_cql(since), None
        while True:
            page = await self._search(cql, limit=self.page_size, cursor=cursor)
            events, newest = [], None
            for hit in page.items:
                if hit.content_id is None or hit.last_modified is None:
                    continue
                newest = max(newest, hit.last_modified) if newest else hit.last_modified
                entity = EntityType.BLOGPOST if hit.type == "blogpost" else EntityType.PAGE
                events.append(poll_hit_event(source=self.kind, product=Product.CONFLUENCE, entity=entity, entity_id=hit.content_id,
                                             version=hit.last_modified.isoformat(), key=hit.title, container=hit.space_key, created=False,
                                             method_id=self.method_id, backend=self.transport, received_at=self._clock()))
            yield PollBatch(events, newest)
            if not page.next_cursor:
                return
            cursor = page.next_cursor
