"""DM-37 (and DM-48 over MCP): Confluence CQL polling (CqlPollingSource: lastmodified, ids only, checkpoints, dedup on id:version)."""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from atlian_assiant.capabilities import BackendKind
from atlian_assiant.events.catalog import method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.model import EntityType, EventSourceKind
from atlian_assiant.events.pipeline import EventPipeline
from atlian_assiant.events.sources import CqlPollingSource, EventSource
from atlian_assiant.events.stores import MemoryCheckpointStore, MemoryDedupStore
from atlian_assiant.events.verify import EventInbox, verify_dm_37
from atlian_assiant.models.common import Page
from atlian_assiant.models.confluence import SearchHit
from atlian_assiant.testing.server import ScenarioServer

from ..conftest import make_rest_client
from .sitesim import SiteSim

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


class FakeConfluence:
    def __init__(self, tz: str = "UTC") -> None:
        self.tz, self.pages, self.queries = ZoneInfo(tz), [], []

    def add(self, content_id: str, modified: datetime, type_: str = "page") -> None:
        self.pages = [p for p in self.pages if p.content_id != content_id]
        self.pages.append(SearchHit(content_id=content_id, type=type_, title=f"Page {content_id}", space_key="ENG", last_modified=modified))

    async def search(self, cql: str, *, limit: int, cursor: str | None) -> Page[SearchHit]:
        self.queries.append(cql)
        stamp = re.search(r'lastmodified >= "([\d-]+ [\d:]+)"', cql).group(1)
        since = datetime.strptime(stamp, "%Y-%m-%d %H:%M").replace(tzinfo=self.tz)
        hits = sorted((p for p in self.pages if p.last_modified.replace(second=0, microsecond=0) >= since), key=lambda p: p.last_modified)
        start = int(cursor or 0)
        return Page(items=hits[start:start + limit], next_cursor=str(start + limit) if start + limit < len(hits) else None)


def poller(conf: FakeConfluence, cp=None, **kw) -> CqlPollingSource:
    return CqlPollingSource(conf.search, "space = ENG", cp or MemoryCheckpointStore(), timezone=str(conf.tz), clock=lambda: T0, **kw)


def test_dm_37_catalog_entry():
    m = method("DM-37")
    assert m.implemented and m.provisioner is None and isinstance(poller(FakeConfluence()), EventSource) and "DM-48" in CqlPollingSource.method_ids


async def test_dm_37_query_uses_lastmodified_with_minute_precision_overlap_and_ordering():
    conf, cp = FakeConfluence(), MemoryCheckpointStore()
    await cp.save(poller(conf).stream, datetime(2026, 9, 1, 12, 30, 59, tzinfo=UTC).isoformat())
    await poller(conf, cp).poll_once()
    assert conf.queries == ['(space = ENG) AND lastmodified >= "2026-09-01 12:25" ORDER BY lastmodified ASC']


async def test_dm_37_emits_page_ids_with_the_modification_time_as_version_and_advances_the_checkpoint():
    conf, cp = FakeConfluence(), MemoryCheckpointStore()
    src = poller(conf, cp)
    await src.poll_once()
    conf.add("100", T0 + timedelta(minutes=1))
    conf.add("200", T0 + timedelta(minutes=2), "blogpost")
    events = await src.poll_once()
    assert [(e.entity_type, e.entity_id) for e in events] == [(EntityType.PAGE, "100"), (EntityType.BLOGPOST, "200")]
    assert events[0].event_id == f"100:{(T0 + timedelta(minutes=1)).isoformat()}" and events[0].source is EventSourceKind.CQL_POLL
    assert events[0].container_key == "ENG" and events[0].delivery.method_id == "DM-37" and datetime.fromisoformat(await cp.load(src.stream)) == T0 + timedelta(minutes=2)


async def test_dm_37_overlap_rereads_are_deduplicated():
    conf = FakeConfluence()
    src = poller(conf)
    await src.poll_once()
    conf.add("100", T0 + timedelta(minutes=1))
    pipe = EventPipeline([src], dedup=MemoryDedupStore())
    seen = []

    @pipe.on()
    async def handle(ev):
        seen.append(ev.event_id)
    for _ in range(3):
        for ev in await src.poll_once():
            await pipe.process(ev)
    assert len(seen) == 1 and pipe.stats.duplicates == 2


async def test_dm_37_time_zone_and_paging():
    conf = FakeConfluence("Asia/Tokyo")
    src = poller(conf, page_size=2)
    await src.poll_once()
    for n in range(1, 6):
        conf.add(str(n), T0 + timedelta(minutes=n))
    assert [e.entity_id for e in await src.poll_once()] == ["1", "2", "3", "4", "5"] and len(conf.queries) == 3 and 'lastmodified >= "2026-09-01 20:55"' in conf.queries[0]


async def test_dm_37_mcp_transport_is_dm_48():
    conf = FakeConfluence()
    src = poller(conf, transport=BackendKind.MCP)
    await src.poll_once()
    conf.add("1", T0 + timedelta(minutes=1))
    ev = (await src.poll_once())[0]
    assert ev.delivery.method_id == "DM-48" and ev.delivery.transport_backend is BackendKind.MCP


async def test_dm_37_hits_without_id_or_time_are_skipped_not_fatal():
    async def search(cql, *, limit, cursor):
        return Page(items=[SearchHit(content_id=None, last_modified=T0), SearchHit(content_id="1", last_modified=None), SearchHit(content_id="2", last_modified=T0 + timedelta(minutes=1))])
    src = CqlPollingSource(search, "space = ENG", MemoryCheckpointStore(), clock=lambda: T0)
    await src.poll_once()
    assert [e.entity_id for e in await src.poll_once()] == ["2"]


async def test_dm_37_from_client_polls_cql_through_the_router_with_excerpts_off():
    server = ScenarioServer()
    client = make_rest_client(server)
    cp = MemoryCheckpointStore()
    src = CqlPollingSource.from_client(client, 'space = "ENG"', cp, clock=lambda: T0)
    await src.poll_once()
    await cp.save(src.stream, (T0 - timedelta(days=400)).isoformat())
    events = await src.poll_once()
    assert events and all(e.delivery.transport_backend is BackendKind.REST for e in events)
    sent = next(r for r in server.requests if "/search" in r["path"] and r["query"].get("cql"))
    assert sent["query"]["cql"].startswith('(space = "ENG") AND lastmodified >= "') and sent["query"]["cql"].endswith("ORDER BY lastmodified ASC")


async def test_dm_37_end_to_end_verify():
    conf = FakeConfluence()
    src = poller(conf)
    await src.poll_once()
    inbox = EventInbox()

    async def on_page(page_id):
        conf.add(page_id, T0 + timedelta(minutes=3))
        for ev in await src.poll_once():
            await inbox.push(ev)
    report = await verify_dm_37(client=SiteSim(on_page=on_page), config=EventPlaneConfig(probe_page="777"), inbox=inbox, timeout=timedelta(seconds=2))
    assert report.ok and report.method_id == "DM-37"
