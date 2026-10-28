"""DM-08: Forge Dynamic Triggers (ForgeSubscriptionManager through the inbox /subscriptions; ForgeRemoteReceiver consumers via DM-07/09)."""
from __future__ import annotations

import pytest

from atlian_assiant.backends.forge.generator import MAX_DYNAMIC_MODULES
from atlian_assiant.core.errors import InvalidRequestError
from atlian_assiant.events.catalog import method
from atlian_assiant.events.provision.base import EventProvisioner
from atlian_assiant.events.provision.forge import ForgeSubscriptionManager
from atlian_assiant.events.verify import verify_dm_08
from atlian_assiant.policy import CallbackConfirmation, DenyAll

from .forgesim import FakeInboxApp

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)
EVENTS = ["avi:jira:updated:issue"]


def test_dm_08_catalog_entry():
    m = method("DM-08")
    assert m.preview and m.provisioner.endswith("ForgeSubscriptionManager") and isinstance(ForgeSubscriptionManager(FakeInboxApp(), events=EVENTS), EventProvisioner)


async def test_dm_08_provision_adds_a_dynamic_trigger_module_as_the_app():
    app = FakeInboxApp()
    mgr = ForgeSubscriptionManager(app, events=EVENTS, key="dyn")
    plan = await mgr.plan()
    assert plan.method_id == "DM-08" and any("VER-07" in w for w in plan.warnings) and any("100 per installation" in w for w in plan.warnings)
    out = await mgr.apply(plan, confirm=APPROVE)
    assert out["subscriptions"] == "1" and app.subs == [{"key": "dyn", "function": "on-event", "events": EVENTS, "filter": {"ignoreSelf": True}}]


async def test_dm_08_provision_is_idempotent_and_replaces_in_place():
    app = FakeInboxApp()
    first = ForgeSubscriptionManager(app, events=EVENTS, key="dyn")
    await first.apply(await first.plan(), confirm=APPROVE)
    second = ForgeSubscriptionManager(app, events=["avi:jira:created:issue"], key="dyn")
    out = await second.apply(await second.plan(), confirm=APPROVE)
    assert len(app.subs) == 1 and app.subs[0]["events"] == ["avi:jira:created:issue"] and out["subscriptions"] == "1"


async def test_dm_08_provision_enforces_the_100_module_limit_before_adding():
    app = FakeInboxApp()
    app.subs = [{"key": f"k{i}"} for i in range(MAX_DYNAMIC_MODULES)]
    mgr = ForgeSubscriptionManager(app, events=EVENTS)
    with pytest.raises(InvalidRequestError, match="maximum 100"):
        await mgr.apply(await mgr.plan(), confirm=APPROVE)
    assert len(app.subs) == MAX_DYNAMIC_MODULES


async def test_dm_08_provision_declined_changes_nothing_and_teardown_removes():
    app = FakeInboxApp()
    mgr = ForgeSubscriptionManager(app, events=EVENTS, key="dyn")
    with pytest.raises(Exception, match="declined"):
        await mgr.apply(await mgr.plan(), confirm=DenyAll())
    assert app.subs == []
    await mgr.apply(await mgr.plan(), confirm=APPROVE)
    await mgr.teardown(confirm=APPROVE)
    assert app.subs == []


async def test_dm_08_verify_checks_the_subscription_is_listed_and_survives_api_errors():
    app = FakeInboxApp()
    mgr = ForgeSubscriptionManager(app, events=EVENTS, key="dyn")
    assert not (await verify_dm_08(manager=mgr)).ok
    await mgr.apply(await mgr.plan(), confirm=APPROVE)
    ok = await verify_dm_08(manager=mgr)
    assert ok.ok and ok.method_id == "DM-08"
    app.fail_list = True
    assert "cannot list" in (await verify_dm_08(manager=mgr)).detail


def test_dm_08_module_shape_ignores_self_triggered_events():
    assert ForgeSubscriptionManager.module("k", EVENTS, "fn")["filter"] == {"ignoreSelf": True}
