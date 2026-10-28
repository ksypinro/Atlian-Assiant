"""DM-36 (and DM-48 over MCP): Jira REST JQL polling (JqlPollingSource: overlap, minute precision, time zone, checkpoints, dedup)."""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from atlian_assiant.capabilities import BackendKind
from atlian_assiant.core.errors import ConfigurationError
from atlian_assiant.events.catalog import method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.model import ChangeAction, EntityType, EventSourceKind
from atlian_assiant.events.pipeline import EventPipeline
from atlian_assiant.events.sources import EventSource, JqlPollingSource
from atlian_assiant.events.stores import MemoryCheckpointStore, MemoryDedupStore
from atlian_assiant.events.verify import EventInbox, verify_dm_36
from atlian_assiant.models.common import Page
from atlian_assiant.models.jira import Issue, ProjectRef
from atlian_assiant.testing.server import ScenarioServer

from ..conftest import make_rest_client
from .sitesim import SiteSim

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


class FakeJira:
    """Evaluates the poller's JQL the way Jira does: minute precision, in a given user time zone; pages through ``cursor``."""

    def __init__(self, tz: str = "UTC") -> None:
        self.tz, self.issues, self.queries = ZoneInfo(tz), [], []

    def add(self, key: str, updated: datetime, created: datetime | None = None) -> None:
        self.issues = [i for i in self.issues if i.key != key]
        self.issues.append(Issue(id=key.split("-")[1], key=key, updated=updated, created=created or updated - timedelta(days=3), project=ProjectRef(id="1", key="PROJ")))

    async def search(self, jql: str, *, limit: int, cursor: str | None) -> Page[Issue]:
        self.queries.append(jql)
        stamp = re.search(r'updated >= "([\d-]+ [\d:]+)"', jql).group(1)
        since = datetime.strptime(stamp, "%Y-%m-%d %H:%M").replace(tzinfo=self.tz)
        hits = sorted((i for i in self.issues if i.updated.replace(second=0, microsecond=0) >= since), key=lambda i: (i.updated, i.key))
        start = int(cursor or 0)
        chunk = hits[start:start + limit]
        return Page(items=chunk, next_cursor=str(start + limit) if start + limit < len(hits) else None)


def poller(jira: FakeJira, cp=None, now=T0, **kw) -> JqlPollingSource:
    return JqlPollingSource(jira.search, "project = PROJ", cp or MemoryCheckpointStore(), timezone=str(jira.tz), clock=lambda: now, **kw)


def test_dm_36_catalog_entry():
    m = method("DM-36")
    assert m.implemented and m.provisioner is None and isinstance(poller(FakeJira()), EventSource) and "DM-48" in JqlPollingSource.method_ids


async def test_dm_36_first_run_starts_from_now_and_does_not_replay_history():
    jira = FakeJira()
    jira.add("PROJ-1", T0 - timedelta(days=1))
    cp = MemoryCheckpointStore()
    src = poller(jira, cp)
    assert await src.poll_once() == [] and await cp.load(src.stream) == T0.isoformat() and jira.queries == []


async def test_dm_36_emits_changes_after_the_checkpoint_and_advances_it():
    jira, cp = FakeJira(), MemoryCheckpointStore()
    src = poller(jira, cp)
    await src.poll_once()
    jira.add("PROJ-1", T0 + timedelta(minutes=1))
    jira.add("PROJ-2", T0 + timedelta(minutes=2))
    events = await src.poll_once()
    assert [e.entity_key for e in events] == ["PROJ-1", "PROJ-2"] and all(e.source is EventSourceKind.JQL_POLL and e.entity_type is EntityType.ISSUE for e in events)
    assert events[0].event_id == f"1:{(T0 + timedelta(minutes=1)).isoformat()}" and events[0].action is ChangeAction.UPDATED and events[0].delivery.method_id == "DM-36"
    assert datetime.fromisoformat(await cp.load(src.stream)) == T0 + timedelta(minutes=2)


async def test_dm_36_query_shape_has_overlap_minute_precision_and_stable_ordering():
    jira, cp = FakeJira(), MemoryCheckpointStore()
    await cp.save(poller(jira).stream, datetime(2026, 9, 1, 12, 30, 45, tzinfo=UTC).isoformat())
    await poller(jira, cp).poll_once()
    assert jira.queries == ['(project = PROJ) AND updated >= "2026-09-01 12:25" ORDER BY updated ASC, key ASC']        # 12:30:45 - 5 min overlap, minute precision


async def test_dm_36_the_checkpoint_is_formatted_in_the_api_users_time_zone():
    berlin = FakeJira("Europe/Berlin")
    cp = MemoryCheckpointStore()
    src = poller(berlin, cp)
    await cp.save(src.stream, datetime(2026, 9, 1, 12, 30, tzinfo=UTC).isoformat())
    await src.poll_once()
    assert 'updated >= "2026-09-01 14:25"' in berlin.queries[0]                                                       # UTC+2 in September
    berlin.add("PROJ-1", datetime(2026, 9, 1, 12, 40, tzinfo=UTC))
    assert [e.entity_key for e in await src.poll_once()] == ["PROJ-1"]                                                 # found: no off-by-hours loss
    wrong = FakeJira("UTC")
    wrong.add("PROJ-1", datetime(2026, 9, 1, 12, 40, tzinfo=UTC))
    naive = poller(wrong)
    await naive.checkpoints.save(naive.stream, datetime(2026, 9, 1, 12, 30, tzinfo=UTC).isoformat())
    assert len(await naive.poll_once()) == 1


async def test_dm_36_overlap_duplicates_are_absorbed_by_dedup_on_id_and_updated():
    jira, cp = FakeJira(), MemoryCheckpointStore()
    src = poller(jira, cp)
    await src.poll_once()
    jira.add("PROJ-1", T0 + timedelta(minutes=1))
    pipe = EventPipeline([src], dedup=MemoryDedupStore())
    seen = []

    @pipe.on()
    async def handle(ev):
        seen.append(ev.event_id)
    for _ in range(3):                                             # the 5-minute overlap re-reads PROJ-1 on every sweep
        for ev in await src.poll_once():
            await pipe.process(ev)
    assert len(seen) == 1 and pipe.stats.duplicates == 2
    jira.add("PROJ-1", T0 + timedelta(minutes=3))                  # a real new change has a new version, so it gets through
    for ev in await src.poll_once():
        await pipe.process(ev)
    assert len(seen) == 2


async def test_dm_36_pages_through_all_results_and_checkpoints_each_page():
    jira, cp = FakeJira(), MemoryCheckpointStore()
    src = poller(jira, cp, page_size=2)
    await src.poll_once()
    for n in range(1, 6):
        jira.add(f"PROJ-{n}", T0 + timedelta(minutes=n))
    assert [e.entity_key for e in await src.poll_once()] == [f"PROJ-{n}" for n in range(1, 6)] and len(jira.queries) == 3


async def test_dm_36_a_new_issue_is_marked_created():
    jira = FakeJira()
    src = poller(jira)
    await src.poll_once()
    jira.add("PROJ-9", T0 + timedelta(minutes=1), created=T0 + timedelta(minutes=1))
    assert (await src.poll_once())[0].action is ChangeAction.CREATED


async def test_dm_36_mcp_transport_is_dm_48_and_the_backend_is_recorded():
    jira = FakeJira()
    src = poller(jira, transport=BackendKind.MCP)
    await src.poll_once()
    jira.add("PROJ-1", T0 + timedelta(minutes=1))
    ev = (await src.poll_once())[0]
    assert src.method_id == "DM-48" and ev.delivery.method_id == "DM-48" and ev.delivery.transport_backend is BackendKind.MCP


async def test_dm_36_lag_reports_how_far_behind_the_poller_is():
    jira, cp = FakeJira(), MemoryCheckpointStore()
    now = {"t": T0}
    src = JqlPollingSource(jira.search, "project = PROJ", cp, clock=lambda: now["t"])
    assert await src.lag() is None
    await src.poll_once()
    now["t"] = T0 + timedelta(minutes=12)
    assert await src.lag() == timedelta(minutes=12)


def test_dm_36_rejects_an_unknown_time_zone():
    with pytest.raises(ConfigurationError, match="Mars"):
        JqlPollingSource(FakeJira().search, "x", MemoryCheckpointStore(), timezone="Mars/Olympus")


async def test_dm_36_from_client_reads_the_users_time_zone_and_polls_through_the_router():
    server = ScenarioServer()
    client = make_rest_client(server)
    cp = MemoryCheckpointStore()
    src = await JqlPollingSource.from_client(client, "project = PROJ", cp, clock=lambda: T0, timezone="Europe/Berlin")
    await src.poll_once()
    await cp.save(src.stream, (T0 - timedelta(days=30)).isoformat())
    events = await src.poll_once()
    assert events and all(e.delivery.transport_backend is BackendKind.REST for e in events)
    sent = next(r["json"] for r in server.requests if r["path"].endswith("/search/jql"))
    assert sent["jql"] == '(project = PROJ) AND updated >= "2026-08-02 13:55" ORDER BY updated ASC, key ASC' and sent["fields"][0] == "updated"


async def test_dm_36_events_loop_sleeps_between_sweeps_and_stops_when_closed():
    jira = FakeJira()
    sleeps = []
    holder = {}

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        jira.add("PROJ-1", T0 + timedelta(minutes=len(sleeps)))       # a change appears while the poller sleeps
        if len(sleeps) == 3:
            await holder["src"].aclose()
    src = holder["src"] = JqlPollingSource(jira.search, "project = PROJ", MemoryCheckpointStore(), clock=lambda: T0, interval=7, sleep=fake_sleep)
    got = [ev async for ev in src.events()]
    assert sleeps == [7, 7, 7] and [e.entity_key for e in got] == ["PROJ-1", "PROJ-1"]
    await src.ack(got[0])
    await src.nack(got[0], error=RuntimeError("x"))


async def test_dm_36_end_to_end_verify_sees_the_label_toggle_as_a_polled_change():
    jira = FakeJira()
    src = poller(jira)
    await src.poll_once()
    inbox = EventInbox()

    async def on_issue(key):
        jira.add(key, T0 + timedelta(minutes=2))
        for ev in await src.poll_once():
            await inbox.push(ev)
    report = await verify_dm_36(client=SiteSim(on_issue=on_issue), config=EventPlaneConfig(probe_issue="PROJ-4"), inbox=inbox, timeout=timedelta(seconds=2))
    assert report.ok and report.method_id == "DM-36"
