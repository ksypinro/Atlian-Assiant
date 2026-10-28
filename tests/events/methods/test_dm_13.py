"""DM-13: organization audit-log webhooks (AuditWebhookProvisioner guided/browser, AuditWebhookReceiver, AuditGapReconciler, verify_dm_13)."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from atlian_assiant.capabilities import Product
from atlian_assiant.core.errors import InvalidRequestError, PolicyDeniedError, UnsupportedOperationError
from atlian_assiant.events.catalog import Ingress, method
from atlian_assiant.events.model import ChangeAction, ChangeEvent, EntityType, EventSourceKind
from atlian_assiant.events.provision.base import ProvisionMode
from atlian_assiant.events.provision.org_audit import MAX_AUDIT_WEBHOOKS, AuditWebhookProvisioner
from atlian_assiant.events.receivers import InboundRequest, StaticHeaderVerifier, WebhookUrlPreflight
from atlian_assiant.events.receivers.audit import AuditGapReconciler, AuditWebhookReceiver
from atlian_assiant.events.receivers.preflight import ProbeResult
from atlian_assiant.events.stores import MemoryLocalQueue
from atlian_assiant.events.verify import EventInbox, verify_dm_13
from atlian_assiant.models.admin import audit_event_from_api
from atlian_assiant.models.common import Page
from atlian_assiant.policy import CallbackConfirmation
from atlian_assiant.testing.server import ScenarioServer

from ..conftest import make_org_client

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
URL = "https://hooks.example.com/hooks/audit"
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
APPROVE = CallbackConfirmation(lambda plan: True)


def public() -> WebhookUrlPreflight:
    async def resolve(host, port):
        return ["8.8.8.8"]

    async def probe(url):
        return ProbeResult(True, status=401)
    return WebhookUrlPreflight(resolver=resolve, prober=probe)


def body(i: int, minute: int = 0) -> bytes:
    return json.dumps({"id": f"ev{i}", "attributes": {"time": f"2026-09-01T11:{minute:02d}:00Z", "action": "user_created", "actor": {"id": "acc-ann"}}}).encode()


def test_dm_13_catalog_entry():
    m = method("DM-13")
    assert m.phase == "P4" and m.ingress is Ingress.REQUIRED and m.entitlements and m.consumer.endswith("AuditWebhookReceiver")


async def test_dm_13_provision_is_guided_only_and_lists_the_admin_ui_steps():
    prov = AuditWebhookProvisioner(None, callback_url=URL, authorization="Bearer x", preflight=public())
    with pytest.raises(PolicyDeniedError):
        await prov.plan(mode=ProvisionMode.API)
    plan = await prov.plan()
    assert plan.method_id == "DM-13" and any("3 times" in w and "gap reconciler" in w for w in plan.warnings)
    out = await prov.apply(plan, confirm=APPROVE)
    assert URL in out["instructions"] and "Authorization header" in out["instructions"] and "Bearer x" not in out["instructions"]


async def test_dm_13_provision_enforces_the_three_webhook_limit_entitlement_and_url_preflight():
    full = AuditWebhookProvisioner(None, callback_url=URL, existing=MAX_AUDIT_WEBHOOKS, preflight=public())
    with pytest.raises(InvalidRequestError, match="maximum 3"):
        await full.apply(await full.plan(), confirm=APPROVE)
    unentitled = AuditWebhookProvisioner(None, callback_url=URL, preflight=public(), entitled=False)
    with pytest.raises(InvalidRequestError, match="DM-12"):
        await unentitled.apply(await unentitled.plan(), confirm=APPROVE)

    async def private(host, port):
        return ["10.0.0.9"]
    bad = AuditWebhookProvisioner(None, callback_url=URL, preflight=WebhookUrlPreflight(resolver=private))
    with pytest.raises(InvalidRequestError, match="callback URL"):
        await bad.apply(await bad.plan(), confirm=APPROVE)


async def test_dm_13_provision_browser_mode_needs_opt_in_and_the_browser_backend():
    with pytest.raises(PolicyDeniedError):
        await AuditWebhookProvisioner(None, callback_url=URL, preflight=public()).plan(mode=ProvisionMode.BROWSER)
    opted = AuditWebhookProvisioner(None, callback_url=URL, preflight=public(), allow_browser_writes=True)
    with pytest.raises(UnsupportedOperationError, match="P5"):
        await opted.apply(await opted.plan(mode=ProvisionMode.BROWSER), confirm=APPROVE)


async def test_dm_13_consumer_verifies_the_header_dedups_on_event_id_and_normalizes():
    q = MemoryLocalQueue()
    ep = AuditWebhookReceiver(q, verifiers=[StaticHeaderVerifier("Authorization", "Bearer tok")])
    ok = {"Authorization": "Bearer tok"}
    statuses = [(await ep.handle(InboundRequest("POST", "/hooks/audit", h, body(1)))).status for h in (ok, ok, {}, {"Authorization": "Bearer no"})]
    assert statuses == [202, 200, 401, 401]
    ev = (await q.get())[1]
    assert (ev.event_id, ev.source, ev.entity_type, ev.action, ev.native_type, ev.actor_account_id) == (
        "ev1", EventSourceKind.AUDIT_WEBHOOK, EntityType.AUDIT, ChangeAction.PERFORMED, "user_created", "acc-ann") and ev.delivery.method_id == "DM-13"
    assert ep.seen_ids == {"ev1"}


class FakeEvents:
    def __init__(self, ids_minutes):
        self.items = [audit_event_from_api({"id": i, "attributes": {"time": f"2026-09-01T11:{m:02d}:00Z", "action": "login"}}) for i, m in ids_minutes]
        self.calls = []

    async def __call__(self, **kw):
        self.calls.append(kw)
        return Page(items=self.items)


async def test_dm_13_gap_reconciler_recovers_events_the_webhook_never_delivered():
    api = FakeEvents([("ev1", 0), ("ev2", 10), ("ev3", 20), ("ev4", 55)])
    rec = AuditGapReconciler(api, lambda: {"ev1", "ev3"}, grace=timedelta(minutes=10), clock=lambda: NOW)
    report, recovered = await rec.reconcile(timedelta(hours=1))
    assert report.missing == ("ev2",) and report.via_webhook == 2 and report.via_api == 4        # ev4 is inside the grace period: webhooks may still lag
    assert [e.event_id for e in recovered] == ["ev2"] and recovered[0].source is EventSourceKind.AUDIT_WEBHOOK and recovered[0].delivery.method_id == "DM-13"
    assert api.calls[0]["start"] == NOW - timedelta(hours=1) and api.calls[0]["end"] == NOW


async def test_dm_13_gap_reconciler_with_nothing_missing_is_quiet_and_pages_through_the_client():
    server = ScenarioServer()
    server.org_events = [{"id": f"ev{i}", "attributes": {"time": "2026-09-01T10:00:00Z", "action": "login"}} for i in range(3)]
    rec = AuditGapReconciler.from_client(make_org_client(server), lambda: {"ev0", "ev1", "ev2"}, clock=lambda: NOW)
    report, recovered = await rec.reconcile()
    assert report.missing == () and recovered == [] and report.via_api == 3


async def test_dm_13_end_to_end_cross_check():
    server = ScenarioServer()
    inbox = EventInbox()
    await inbox.push(ChangeEvent(event_id=server.org_events[0]["id"], source=EventSourceKind.AUDIT_WEBHOOK, product=Product.PLATFORM, entity_type=EntityType.AUDIT,
                                 action=ChangeAction.PERFORMED, native_type="x", received_at=NOW))
    report = await verify_dm_13(client=make_org_client(server), inbox=inbox, timeout=timedelta(seconds=1))
    assert report.ok and "audit_webhook" in report.detail
