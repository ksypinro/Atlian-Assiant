"""DM-35: JSM incident / on-call notifications (JsmOpsProvisioner, JsmOpsAlertSource + JsmOpsClient, verify_dm_35)."""
from __future__ import annotations

import pytest

from atlian_assiant.core.errors import AuthenticationError, InvalidRequestError
from atlian_assiant.events.catalog import method
from atlian_assiant.events.model import ChangeAction, EntityType
from atlian_assiant.events.provision.base import EventProvisioner
from atlian_assiant.events.provision.jsm_ops import JsmOpsProvisioner
from atlian_assiant.events.sources import EventSource, JsmOpsAlertSource, JsmOpsClient
from atlian_assiant.events.stores import MemoryCheckpointStore
from atlian_assiant.events.verify import verify_dm_35
from atlian_assiant.policy import CallbackConfirmation

from .humansim import ops_http

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)


def client():
    http, state = ops_http()
    return state, JsmOpsClient(http, "cid-1", {"Authorization": "Bearer t"})


def alert(i, status="open", created="2026-09-01T10:00:00Z", updated="2026-09-01T10:00:00Z"):
    return {"id": f"al{i}", "alias": f"inc-{i}", "message": f"Database down {i}", "status": status, "createdAt": created, "updatedAt": updated, "priority": "P1"}


def test_dm_35_catalog_entry():
    m = method("DM-35")
    assert m.entitlements == ("JSM Operations (on-call)",) and isinstance(JsmOpsProvisioner(), EventProvisioner)


async def test_dm_35_client_lists_creates_acknowledges_closes_and_looks_up_on_call():
    state, ops = client()
    created = await ops.create_alert("hello", alias="a", priority="P2")
    await ops.acknowledge(created["id"])
    await ops.close(created["id"])
    assert state["acked"] == [created["id"]] and state["closed"] == [created["id"]] and await ops.on_call("sch1") == ["ann@example.com"]
    assert (await ops.list_alerts())[0]["status"] == "closed" and [r for r in state["requests"] if r[0] == "GET" and r[1] == "/alerts"][0][2]["sort"] == "updatedAt"


async def test_dm_35_provision_is_guided_without_a_client_and_checks_reachability_with_one():
    guided = JsmOpsProvisioner()
    plan = await guided.plan()
    assert plan.method_id == "DM-35" and plan.outputs["mode"] == "guided" and any("VER-22" in w and "Operations" in w for w in plan.warnings)
    assert "Integrations" in (await guided.apply(plan, confirm=APPROVE))["instructions"]
    state, ops = client()
    state["alerts"].append(alert(1))
    prov = JsmOpsProvisioner(ops, schedule_id="sch1")
    out = await prov.apply(await prov.plan(), confirm=APPROVE)
    assert out["alerts_reachable"] == "true" and out["visible_alerts"] == "1" and out["on_call"] == "ann@example.com"


async def test_dm_35_provision_explains_an_unreachable_operations_api():
    class Broken:
        async def list_alerts(self, **kw):
            raise AuthenticationError("401")
    prov = JsmOpsProvisioner(Broken())
    with pytest.raises(InvalidRequestError, match="not reachable"):
        await prov.apply(await prov.plan(), confirm=APPROVE)


async def test_dm_35_consumer_maps_alerts_to_created_updated_closed_with_id_updated_dedup_and_a_checkpoint():
    state, ops = client()
    cp = MemoryCheckpointStore()
    src = JsmOpsAlertSource.from_client(ops, cp)
    assert isinstance(src, EventSource) and src.method_ids == {"DM-35"}
    state["alerts"] += [alert(1), alert(2, created="2026-09-01T09:00:00Z", updated="2026-09-01T10:01:00Z"), alert(3, status="closed", created="2026-09-01T08:00:00Z", updated="2026-09-01T10:02:00Z")]
    events = await src.poll_once()
    assert [(e.event_id, e.entity_type, e.action, e.entity_key) for e in events] == [
        ("al1:2026-09-01T10:00:00Z", EntityType.ALERT, ChangeAction.CREATED, "inc-1"), ("al2:2026-09-01T10:01:00Z", EntityType.ALERT, ChangeAction.UPDATED, "inc-2"),
        ("al3:2026-09-01T10:02:00Z", EntityType.ALERT, ChangeAction.CLOSED, "inc-3")]
    assert events[0].payload["priority"] == "P1" and events[2].native_type == "jsm:alert:closed" and await cp.load(src.stream) == "2026-09-01T10:02:00Z"
    assert await src.poll_once() == [] and "updatedAt > 2026-09-01T10:02:00Z" in state["requests"][-1][2]["query"]
    state["alerts"].append(alert(2, status="closed", created="2026-09-01T09:00:00Z", updated="2026-09-01T10:03:00Z"))
    assert [e.action for e in await src.poll_once()] == [ChangeAction.CLOSED]


async def test_dm_35_end_to_end_verify_creates_lists_and_closes_a_labelled_probe_alert():
    state, ops = client()
    report = await verify_dm_35(ops=ops)
    assert report.ok and report.method_id == "DM-35" and state["closed"] == ["al1"] and state["alerts"][0]["message"] == "atlian-assiant verify probe"
    class Down:
        async def create_alert(self, *a, **k):
            raise AuthenticationError("401")
    assert not (await verify_dm_35(ops=Down())).ok
