"""DM-12: organization /events-stream cursor polling (OrgAuditProvisioner, OrgEventsStreamSource, verify_dm_12)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from atlian_assiant.capabilities import Product
from atlian_assiant.events.catalog import method
from atlian_assiant.events.model import ChangeAction, ChangeEvent, EntityType, EventSourceKind
from atlian_assiant.events.pipeline import EventPipeline
from atlian_assiant.events.provision.org_audit import OrgAuditProvisioner
from atlian_assiant.events.sources import EventSource
from atlian_assiant.events.sources.org_audit import OrgEventsStreamSource
from atlian_assiant.events.stores import MemoryCheckpointStore, MemoryDedupStore
from atlian_assiant.events.verify import EventInbox, verify_dm_12
from atlian_assiant.models.admin import AuditEventPage, audit_event_from_api
from atlian_assiant.policy import CallbackConfirmation
from atlian_assiant.testing.server import ScenarioServer

from ..conftest import make_org_client

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
T0 = datetime(2026, 9, 1, 10, 30, tzinfo=UTC)
APPROVE = CallbackConfirmation(lambda plan: True)


def raw(i: int) -> dict:
    return {"id": f"ev{i}", "attributes": {"time": "2026-09-01T10:00:00Z", "processedAt": "2026-09-01T10:00:05Z", "action": "login", "actor": {"id": "a"}}}


class FakeStream:
    """A cursor-ordered stream: cursor = number of events consumed; the cursor is returned even when nothing is new."""

    def __init__(self, n=0):
        self.events, self.calls = [raw(i) for i in range(n)], []

    async def __call__(self, *, cursor=None, start=None, limit=500):
        self.calls.append((cursor, start, limit))
        pos = int(cursor) if cursor else 0
        chunk = self.events[pos:pos + limit]
        return AuditEventPage(events=[audit_event_from_api(e) for e in chunk], next_cursor=str(pos + len(chunk)))


def test_dm_12_catalog_entry():
    m = method("DM-12")
    assert m.phase == "P4" and m.consumer.endswith("OrgEventsStreamSource") and isinstance(OrgEventsStreamSource(FakeStream(), MemoryCheckpointStore()), EventSource)


async def test_dm_12_provision_stores_the_starting_cursor_so_history_is_not_replayed():
    server = ScenarioServer()
    cp = MemoryCheckpointStore()
    out = await OrgAuditProvisioner(make_org_client(server), cp).apply(await OrgAuditProvisioner(make_org_client(server), cp).plan(), confirm=APPROVE)
    assert out["cursor"] == "stored" and (await cp.load("org-events-stream")) is not None


async def test_dm_12_consumer_checkpoints_the_cursor_after_every_page_and_resumes_from_it():
    api, cp = FakeStream(5), MemoryCheckpointStore()
    src = OrgEventsStreamSource(api, cp, page_size=2, clock=lambda: T0)
    events = await src.poll_once()
    assert [e.event_id for e in events] == [f"ev{i}" for i in range(5)] and await cp.load("org-events-stream") == "5"
    assert all(e.source is EventSourceKind.ORG_EVENTS_STREAM and e.processed_at is not None and e.entity_type is EntityType.AUDIT
               and e.action is ChangeAction.PERFORMED for e in events)
    assert [c[0] for c in api.calls] == [None, "2", "4", "5"] and api.calls[0][1] == T0         # the first call starts "from now"; later calls use only the cursor
    api.events.append(raw(5))
    assert [e.event_id for e in await src.poll_once()] == ["ev5"] and await cp.load("org-events-stream") == "6"


async def test_dm_12_the_cursor_survives_a_restart_and_an_empty_page_keeps_it():
    api, cp = FakeStream(2), MemoryCheckpointStore()
    await OrgEventsStreamSource(api, cp, clock=lambda: T0).poll_once()
    again = OrgEventsStreamSource(api, cp, clock=lambda: T0)                                       # a new process, same checkpoint store
    assert await again.poll_once() == [] and await cp.load("org-events-stream") == "2"


async def test_dm_12_replays_are_deduplicated_by_event_id_in_the_pipeline():
    api, cp = FakeStream(1), MemoryCheckpointStore()
    src = OrgEventsStreamSource(api, cp, clock=lambda: T0)
    batch = await src.poll_once()
    pipe, seen = EventPipeline([src], dedup=MemoryDedupStore()), []

    async def handle(ev):
        seen.append(ev.event_id)
    pipe.on()(handle)
    for ev in batch * 3:
        await pipe.process(ev)
    assert seen == ["ev0"]


async def test_dm_12_from_client_pages_the_real_wire_format_ordered_by_processing_time():
    server = ScenarioServer()
    cp = MemoryCheckpointStore()
    src = OrgEventsStreamSource.from_client(make_org_client(server), cp, page_size=2, clock=lambda: T0)
    first = await src.poll_once()                       # no cursor yet: starts at the first available page
    assert first and all(r["query"].get("sortOrder") == "asc" for r in server.requests if r["path"].endswith("/events-stream"))
    assert await cp.load("org-events-stream")


async def test_dm_12_end_to_end_cross_check():
    server = ScenarioServer()
    client = make_org_client(server)
    inbox = EventInbox()
    newest = server.org_events[0]["id"]
    await inbox.push(ChangeEvent(event_id=newest, source=EventSourceKind.ORG_EVENTS_STREAM, product=Product.PLATFORM, entity_type=EntityType.AUDIT,
                                 action=ChangeAction.PERFORMED, native_type="x", received_at=T0))
    report = await verify_dm_12(client=client, inbox=inbox, timeout=timedelta(seconds=1))
    assert report.ok and report.method_id == "DM-12"
