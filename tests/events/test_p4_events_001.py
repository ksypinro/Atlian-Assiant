"""EventPlaneFactory: decision guide of EV 60, every blueprint buildable explicitly, EventPlane assembly (design 14.16)."""
from __future__ import annotations

from datetime import timedelta

import anyio
import pytest

from atlian_assiant.capabilities import Deployment, Product
from atlian_assiant.core.errors import InvalidRequestError
from atlian_assiant.events import catalog
from atlian_assiant.events.catalog import Ingress
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.model import ChangeAction, EntityType
from atlian_assiant.events.planner import (
    BLUEPRINT_SUMMARY,
    HUMAN_CHANNEL_METHODS,
    RELAY_BY_CLOUD,
    Blueprint,
    EventPlaneFactory,
    EventRequirements,
)
from atlian_assiant.policy import CallbackConfirmation

from .conftest import make_client

pytestmark = [pytest.mark.layer_11, pytest.mark.anyio]
JIRA, CONF = frozenset({Product.JIRA}), frozenset({Product.CONFLUENCE})


def rec(**kw):
    kw.setdefault("products", JIRA)
    return EventPlaneFactory.recommend(EventRequirements(**kw))


def test_a_public_receiver_gets_the_direct_webhook_and_always_the_reconciler():
    plan = rec(max_ingress=Ingress.REQUIRED)
    assert plan.blueprints == (Blueprint.DIRECT_WEBHOOK,) and plan.methods == ("DM-03", "DM-05") and "DM-38" in plan.reconciliation and "DM-36" in plan.reconciliation
    assert any("Secondary webhook flows" in w for w in plan.warnings)


def test_no_ingress_decision_ladder_for_jira_follows_ev_60_in_order():
    assert rec().blueprints == (Blueprint.ZERO_APP_POLLING,)                                                           # nothing else allowed: polling
    assert rec(forge_allowed=True, preview_features_allowed=True).blueprints == (Blueprint.FORGE_INBOX,) and "Preview" in rec(preview_features_allowed=True).warnings[-1]
    assert rec(products=frozenset({Product.JSM}), jsm_premium_or_enterprise=True).blueprints == (Blueprint.JEC,)
    assert rec(clouds=frozenset({"aws"})).blueprints == (Blueprint.AWS_QUEUE,)
    assert rec(mcp_only_credentials=True).blueprints == (Blueprint.MCP_POLLING,) and rec(mcp_only_credentials=True).methods == ("DM-48",)
    assert rec(processing_inside_atlassian_ok=True, must_process_in_lan=False).blueprints == (Blueprint.FORGE_NATIVE,)


def test_relay_and_tunnel_are_chosen_only_when_the_ingress_policy_allows_them():
    managed = rec(max_ingress=Ingress.MANAGED, clouds=frozenset({"azure"}))
    assert managed.blueprints == (Blueprint.MANAGED_RELAY,) and managed.methods == ("DM-03", "DM-40")
    assert rec(max_ingress=Ingress.MANAGED).blueprints != (Blueprint.MANAGED_RELAY,)                                  # no cloud named: cannot pick a relay
    tunnel = rec(max_ingress=Ingress.TUNNEL)
    assert tunnel.blueprints == (Blueprint.TUNNEL,) and any("strict" in w for w in tunnel.warnings)
    for strict in (rec(), rec(max_ingress=Ingress.NONE, clouds=frozenset({"aws"}))):
        assert Blueprint.TUNNEL not in strict.blueprints and "DM-44" not in strict.methods                           # never under 'no public URL'


def test_confluence_has_no_rest_webhooks_so_forge_automation_or_polling_is_chosen():
    assert rec(products=CONF, preview_features_allowed=True).methods == ("DM-07",)
    assert rec(products=CONF, max_ingress=Ingress.REQUIRED).methods == ("DM-09",)
    assert rec(products=CONF).methods == ("DM-37",) and rec(products=CONF, max_ingress=Ingress.MANAGED, clouds=frozenset({"gcp"}), forge_allowed=False).methods == ("DM-16", "DM-41")
    both = rec(products=frozenset({Product.JIRA, Product.CONFLUENCE}), preview_features_allowed=True)
    assert both.methods == ("DM-07",) and set(both.reconciliation) >= {"DM-38", "DM-36", "DM-37"}


def test_audit_human_channels_live_ui_and_data_center_branches():
    assert rec(need_audit_events=True).methods[-2:] == ("DM-12", "DM-14") and Blueprint.ORG_AUDIT in rec(need_audit_events=True).blueprints
    hook = rec(need_audit_events=True, audit_webhook_entitled=True, max_ingress=Ingress.REQUIRED)
    assert "DM-13" in hook.methods and "DM-11" in hook.reconciliation
    humans = rec(human_channels=frozenset({"slack", "email"}))
    assert Blueprint.HUMAN_NOTIFICATIONS in humans.blueprints and {"DM-21", "DM-20"} <= set(humans.methods)
    assert "DM-10" in rec(live_forge_ui=True).methods and any("not allowed" in w for w in rec(live_forge_ui=True, forge_allowed=False).warnings)
    dc = rec(products=frozenset({Product.JIRA, Product.CONFLUENCE}), deployment=Deployment.DATA_CENTER)
    assert dc.blueprints == (Blueprint.DATA_CENTER_WEBHOOK,) and dc.methods == ("DM-47", "DM-46")


def test_forge_latency_warning_appears_only_when_the_budget_is_tight():
    assert any("3 minutes" in w for w in rec(preview_features_allowed=True, max_latency=timedelta(minutes=1)).warnings)
    assert not any("3 minutes" in w for w in rec(preview_features_allowed=True).warnings)


@pytest.mark.parametrize("blueprint", list(Blueprint))
def test_every_blueprint_can_be_built_explicitly_with_catalogued_methods(blueprint):
    plan = EventPlaneFactory.blueprint(blueprint, products={Product.JIRA, Product.CONFLUENCE}, cloud="gcp", channels=["slack"], dynamic=True)
    assert plan.blueprints == (blueprint,) and plan.methods and BLUEPRINT_SUMMARY[blueprint] in plan.rationale[0]
    assert all(m.startswith("DM-") and catalog.method(m) for m in (*plan.methods, *plan.reconciliation))


def test_explicit_blueprints_use_the_documented_methods_and_params():
    f = EventPlaneFactory.blueprint
    assert f(Blueprint.DIRECT_WEBHOOK).methods == ("DM-03", "DM-05") and f(Blueprint.MANAGED_RELAY, cloud="cloudflare").methods == ("DM-03", "DM-42")
    assert f(Blueprint.FORGE_INBOX, dynamic=True).methods == ("DM-07", "DM-08") and f(Blueprint.FORGE_INBOX).methods == ("DM-07",)
    assert f(Blueprint.ORG_AUDIT).methods == ("DM-12", "DM-11", "DM-14") and f(Blueprint.AUDIT_WEBHOOK).reconciliation == ("DM-11",)
    assert f(Blueprint.ZERO_APP_POLLING, products={Product.CONFLUENCE}).methods == ("DM-37",) and f(Blueprint.DATA_CENTER_WEBHOOK).methods == ("DM-47",)
    assert f(Blueprint.HUMAN_NOTIFICATIONS, channels=["sms"]).methods == HUMAN_CHANNEL_METHODS["sms"]
    assert f(Blueprint.JEC).warnings and "Premium" in f(Blueprint.JEC).warnings[0]
    with pytest.raises(InvalidRequestError, match="cloud"):
        f(Blueprint.MANAGED_RELAY, cloud="mars")
    with pytest.raises(InvalidRequestError, match="channels"):
        f(Blueprint.HUMAN_NOTIFICATIONS, channels=["pigeon"])
    assert set(RELAY_BY_CLOUD) == {"aws", "azure", "gcp", "cloudflare", "broker"}


def test_the_forge_ladder_never_selects_remote_or_tunnel_under_no_ingress():
    for req in (rec(), rec(preview_features_allowed=True), rec(products=CONF, preview_features_allowed=True), rec(products=CONF)):
        assert Blueprint.FORGE_REMOTE not in req.blueprints and Blueprint.TUNNEL not in req.blueprints


async def test_build_assembles_an_event_plane_with_a_durable_store(tmp_path):
    plan = EventPlaneFactory.recommend(EventRequirements(products=JIRA, max_ingress=Ingress.REQUIRED, need_audit_events=True))
    cfg = EventPlaneConfig(store_url=f"sqlite:///{tmp_path}/plane.db", public_callback_base="https://hooks.example.com")
    plane = EventPlaneFactory.build(plan, make_client(), store_url=cfg.store_url, config=cfg)
    info = plane.describe()
    assert info["methods"][:2] == ["DM-03", "DM-05"] and "DM-05" in info["sources"] and info["endpoints"] == ["/hooks/jira"] and "DM-03" in info["provisioners"]
    assert info["skipped"]["DM-14"].startswith("provision") and info["skipped"]["DM-36"].startswith("needs a JQL") and "DM-12" in info["sources"]
    assert callable(plane.asgi_app()) and (tmp_path / "plane.db").exists()


async def test_build_skips_methods_it_cannot_configure_alone_and_says_what_to_do():
    plan = EventPlaneFactory.blueprint(Blueprint.TUNNEL)
    plane = EventPlaneFactory.build(plan, make_client(), store_url="sqlite:///:memory:")
    assert plane.skipped["DM-44"].startswith("provision and consume this method explicitly")


async def test_plane_provisions_with_confirmation_and_runs_events_through_the_pipeline(tmp_path):
    plan = EventPlaneFactory.blueprint(Blueprint.DIRECT_WEBHOOK)
    cfg = EventPlaneConfig(store_url=f"sqlite:///{tmp_path}/p.db", public_callback_base="https://hooks.example.com")
    client = make_client()
    plane = EventPlaneFactory.build(plan, client, store_url=cfg.store_url, config=cfg)
    outputs = {}

    async def preflight_ok(url):
        from atlian_assiant.events.receivers.preflight import PreflightReport
        return PreflightReport(url, True, ())
    plane.provisioners["DM-03"].preflight.check = preflight_ok                               # the plane's provisioner is a normal provisioner
    outputs = await plane.provision(CallbackConfirmation(lambda p: True))
    assert outputs["DM-03"]["created"] == "1" and len(await client.webhooks.list()) == 1
    from atlian_assiant.events.receivers import InboundRequest
    ep = plane.endpoints[0]
    import json
    raw = json.dumps({"webhookEvent": "jira:issue_updated", "issue": {"id": "10001", "key": "PROJ-1", "fields": {"updated": "2026-09-01T12:00:00.000+0000"}}}).encode()
    assert (await ep.handle(InboundRequest("POST", "/hooks/jira", {"X-Atlassian-Webhook-Identifier": "w1"}, raw))).status == 202
    seen = []
    stop = anyio.Event()

    async def handle(ev):
        seen.append((ev.entity_key, ev.action, ev.entity_type, type(ev.canonical).__name__))
        stop.set()
    plane.pipeline.on()(handle)
    with anyio.fail_after(5):
        await plane.run(stop=stop)
    assert seen and seen[0][:3] == ("PROJ-1", ChangeAction.UPDATED, EntityType.ISSUE) and seen[0][3] == "Issue"           # the handler saw FRESH canonical state
    await plane.teardown(CallbackConfirmation(lambda p: True))
    assert await client.webhooks.list() == []
