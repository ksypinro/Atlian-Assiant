"""DM-11: organization Events API /events, /events/{id}, /event-actions (OrgAuditProvisioner, OrgEventsListSource, verify_dm_11)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from atlian_assiant.capabilities import Product
from atlian_assiant.core.errors import InvalidRequestError
from atlian_assiant.events.catalog import method
from atlian_assiant.events.model import ChangeAction, EntityType, EventSourceKind
from atlian_assiant.events.pipeline import EventPipeline
from atlian_assiant.events.provision.base import EventProvisioner
from atlian_assiant.events.provision.org_audit import OrgAuditProvisioner
from atlian_assiant.events.sources import EventSource
from atlian_assiant.events.sources.org_audit import OrgEventsListSource
from atlian_assiant.events.stores import MemoryCheckpointStore, MemoryDedupStore
from atlian_assiant.events.verify import EventInbox, verify_dm_11
from atlian_assiant.policy import CallbackConfirmation
from atlian_assiant.testing.server import ScenarioServer

from ..conftest import make_org_client

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
T0 = datetime(2026, 9, 1, 10, 30, tzinfo=UTC)
APPROVE = CallbackConfirmation(lambda plan: True)


def raw(i: int, action: str = "login", minute: int | None = None) -> dict:
    m = i if minute is None else minute
    return {"id": f"ev{i}", "type": "events", "attributes": {"time": f"2026-09-01T10:{m:02d}:00Z", "action": action, "actor": {"id": "acc-ann"}, "context": []}}


def test_dm_11_catalog_entry():
    m = method("DM-11")
    assert m.phase == "P4" and m.provisioner.endswith("OrgAuditProvisioner") and m.verifier.endswith("verify_dm_11")
    assert m.consumer.endswith("OrgEventsListSource") and isinstance(OrgAuditProvisioner(None, MemoryCheckpointStore()), EventProvisioner)


async def test_dm_11_provision_checks_key_and_access_and_stores_the_cursor():
    server = ScenarioServer()
    cp = MemoryCheckpointStore()
    prov = OrgAuditProvisioner(make_org_client(server), cp, clock=lambda: T0)
    plan = await prov.plan()
    assert plan.method_id == "DM-12" and any("exclusions" in w for w in plan.warnings)
    out = await prov.apply(plan, confirm=APPROVE)
    assert out["actions"] == "2" and out["cursor"] == "stored" and await cp.load("org-events-stream")


async def test_dm_11_provision_explains_a_rejected_key():
    server = ScenarioServer()
    server.fail_next = [(401, {})]
    prov = OrgAuditProvisioner(make_org_client(server), MemoryCheckpointStore())
    with pytest.raises(InvalidRequestError) as exc:
        await prov.apply(await prov.plan(), confirm=APPROVE)
    assert "ORG_API_KEY" in exc.value.hint or "Credentials" in exc.value.hint


async def test_dm_11_provision_never_overwrites_an_existing_checkpoint():
    cp = MemoryCheckpointStore()
    await cp.save("org-events-stream", "42")
    prov = OrgAuditProvisioner(make_org_client(ScenarioServer()), cp)
    await prov.apply(await prov.plan(), confirm=APPROVE)
    assert await cp.load("org-events-stream") == "42"


class FakeList:
    def __init__(self, events):
        self.events, self.calls = events, []

    async def __call__(self, **kw):
        from atlian_assiant.events.sources.org_audit import Page
        from atlian_assiant.models.admin import audit_event_from_api
        self.calls.append(kw)
        items = [audit_event_from_api(e) for e in self.events if kw["start"] <= audit_event_from_api(e).occurred_at <= kw["end"]]
        return Page(items=items)


async def test_dm_11_consumer_first_run_starts_now_then_pulls_windows_with_overlap():
    api, cp = FakeList([raw(1, minute=31), raw(2, minute=32)]), MemoryCheckpointStore()
    now = {"t": T0}
    src = OrgEventsListSource(api, cp, clock=lambda: now["t"])
    assert isinstance(src, EventSource) and await src.poll_once() == []
    now["t"] = T0 + timedelta(minutes=3)
    events = await src.poll_once()
    assert [e.event_id for e in events] == ["ev1", "ev2"] and all(e.source is EventSourceKind.ORG_EVENTS_LIST and e.entity_type is EntityType.AUDIT
                                                                  and e.action is ChangeAction.PERFORMED and e.native_type == "login" for e in events)
    assert api.calls[0]["start"] == T0 - timedelta(minutes=5) and api.calls[0]["end"] == now["t"]


async def test_dm_11_consumer_overlap_rereads_are_deduplicated_by_event_id():
    api, cp = FakeList([raw(1, minute=31)]), MemoryCheckpointStore()
    now = {"t": T0}
    src = OrgEventsListSource(api, cp, clock=lambda: now["t"])
    await src.poll_once()
    pipe, seen = EventPipeline([src], dedup=MemoryDedupStore()), []
    pipe.on()(lambda ev: seen.append(ev.event_id) or _noop())
    for minute in (32, 33, 34):
        now["t"] = T0 + timedelta(minutes=minute - 30)
        for ev in await src.poll_once():
            await pipe.process(ev)
    assert seen == ["ev1"] and pipe.stats.duplicates >= 1


async def _noop():
    return None


async def test_dm_11_consumer_reads_through_the_client_and_pages():
    server = ScenarioServer()
    server.org_events = [raw(i, minute=31) for i in range(1, 6)]
    cp = MemoryCheckpointStore()
    now = {"t": T0}
    src = OrgEventsListSource.from_client(make_org_client(server), cp, clock=lambda: now["t"], page_size=2)
    await src.poll_once()
    now["t"] = T0 + timedelta(minutes=3)
    assert len(await src.poll_once()) == 5 and sum(r["path"].endswith("/events") for r in server.requests) == 3


async def test_dm_11_end_to_end_cross_check_succeeds_when_the_newest_event_arrives_and_fails_otherwise():
    server = ScenarioServer()
    client = make_org_client(server)
    inbox = EventInbox()
    newest = server.org_events[0]["id"]
    from atlian_assiant.events.model import ChangeEvent
    await inbox.push(ChangeEvent(event_id=newest, source=EventSourceKind.ORG_EVENTS_LIST, product=Product.PLATFORM,
                                 entity_type=EntityType.AUDIT, action=ChangeAction.PERFORMED, native_type="x", received_at=T0))
    ok = await verify_dm_11(client=client, inbox=inbox, timeout=timedelta(seconds=1))
    assert ok.ok and ok.method_id == "DM-11" and newest in ok.detail
    missing = await verify_dm_11(client=client, inbox=EventInbox(), timeout=timedelta(milliseconds=30))
    assert not missing.ok and "did not arrive" in missing.detail
    server.org_events = []
    assert "no events yet" in (await verify_dm_11(client=client, inbox=EventInbox(), timeout=timedelta(milliseconds=30))).detail
