"""Provisioner base, plans, EndToEndProbe and EventPlaneConfig (design 14.3)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from atlian_assiant.capabilities import Access
from atlian_assiant.core.errors import ConfirmationDeclinedError, PolicyDeniedError
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.provision.base import (
    EventProvisioner,
    ProvisioningPlan,
    ProvisioningStep,
    ProvisionMode,
    confirm_plan,
    pick_mode,
)
from atlian_assiant.events.provision.jira_webhooks import JiraAdminWebhookProvisioner, JiraDynamicWebhookProvisioner
from atlian_assiant.events.verify import EndToEndProbe, EventInbox, issue_property_trigger, jira_label_trigger
from atlian_assiant.policy import AccessPolicy, CallbackConfirmation, DenyAll

from .conftest import event, make_client

pytestmark = [pytest.mark.layer_11, pytest.mark.anyio]


def test_provisioners_satisfy_the_protocol_and_declare_methods_and_modes():
    for cls in (JiraAdminWebhookProvisioner, JiraDynamicWebhookProvisioner):
        assert cls.method_ids and cls.modes and ProvisionMode.API in cls.modes or ProvisionMode.GUIDED in cls.modes
    p = JiraDynamicWebhookProvisioner(make_client(), callback_url="https://hooks.example.com/x", subscriptions=[])
    assert isinstance(p, EventProvisioner)


def test_plan_renders_every_step_and_marks_irreversible_ones():
    plan = ProvisioningPlan("DM-17", (ProvisioningStep("make a queue", ProvisionMode.API, True, False),
                                      ProvisioningStep("delete the old one", ProvisionMode.API, False, True)), warnings=("careful",))
    text = plan.render()
    assert "DM-17" in text and "make a queue" in text and "NOT reversible" in text and "admin" in text and "! careful" in text


def test_pick_mode_prefers_api_then_guided_and_rejects_unsupported_requests():
    supported = frozenset({ProvisionMode.GUIDED, ProvisionMode.API})
    assert pick_mode(None, supported, (ProvisionMode.API, ProvisionMode.GUIDED)) is ProvisionMode.API
    assert pick_mode(None, frozenset({ProvisionMode.GUIDED}), (ProvisionMode.API, ProvisionMode.GUIDED)) is ProvisionMode.GUIDED
    with pytest.raises(PolicyDeniedError):
        pick_mode(ProvisionMode.BROWSER, supported, (ProvisionMode.API,))


async def test_apply_needs_confirmation_and_a_decline_changes_nothing():
    plan = ProvisioningPlan("DM-03", (ProvisioningStep("register", ProvisionMode.API, True, False),))
    with pytest.raises(ConfirmationDeclinedError):
        await confirm_plan(plan, DenyAll(), operation_id="webhook.jira.dynamic.register", target="x")
    seen = []
    await confirm_plan(plan, CallbackConfirmation(lambda a: seen.append(a) or True), operation_id="webhook.jira.dynamic.register", target="x")
    assert seen and "register" in seen[0].summary and seen[0].reversible is True


def test_event_plane_config_reads_prefixed_environment_and_hides_secrets(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_EVENTS_PROBE_ISSUE", "SBX-1")
    monkeypatch.setenv("ATLASSIAN_EVENTS_WEBHOOK_SECRET", "s3cret")
    cfg = EventPlaneConfig()
    assert cfg.probe_issue == "SBX-1" and cfg.store_url.startswith("sqlite:///")
    assert "s3cret" not in repr(cfg) and cfg.webhook_secret.get_secret_value() == "s3cret"


async def test_probe_without_a_configured_target_changes_nothing():
    client = make_client()
    report = await EndToEndProbe("DM-03", target=None, target_name="probe_issue", trigger=issue_property_trigger(client), inbox=EventInbox()).run()
    assert not report.ok and "probe_issue is not configured" in report.detail and client.seen == []


async def test_probe_touches_only_the_configured_target_and_measures_latency():
    client = make_client()
    inbox = EventInbox()
    touched = []
    ticks = iter([10.0, 12.5])

    async def trigger(target: str, token: str) -> None:
        touched.append(target)
        await inbox.push(event("probe-1", key=target))

    report = await EndToEndProbe("DM-03", target="SBX-1", target_name="probe_issue", trigger=trigger, inbox=inbox, clock=lambda: next(ticks)).run()
    assert report.ok and report.latency_seconds == 2.5 and touched == ["SBX-1"] and "SBX-1" in report.detail and client.seen == []


async def test_probe_reports_a_timeout_with_guidance():
    inbox = EventInbox()

    async def trigger(target: str, token: str) -> None:
        return None

    report = await EndToEndProbe("DM-03", target="SBX-1", target_name="probe_issue", trigger=trigger, inbox=inbox).run(timedelta(milliseconds=50))
    assert not report.ok and "no event for SBX-1" in report.detail


async def test_probe_writes_need_write_access_and_user_confirmation():
    read_only = make_client(access=frozenset({Access.READ, Access.SEARCH}))
    inbox = EventInbox()
    denied = await EndToEndProbe("DM-03", target="PROJ-1", target_name="probe_issue", trigger=issue_property_trigger(read_only), inbox=inbox).run()
    assert not denied.ok and "PolicyDeniedError" in denied.detail
    refusing = make_client(confirm=lambda plan: False)
    declined = await EndToEndProbe("DM-15", target="PROJ-1", target_name="probe_issue", trigger=jira_label_trigger(refusing), inbox=inbox).run()
    assert not declined.ok and "ConfirmationDeclinedError" in declined.detail
    assert len(refusing.seen) == 1                                  # the user was asked before anything changed


async def test_probe_mutations_leave_nothing_behind_on_the_sandbox_issue():
    client = make_client()
    await issue_property_trigger(client)("PROJ-1", "tok")
    assert await client.jira.issues.get_property("PROJ-1", "atlian_assiant.probe") == {"probe": None}
    before = (await client.jira.issues.get("PROJ-1")).labels
    await jira_label_trigger(client)("PROJ-1", "tok")
    assert (await client.jira.issues.get("PROJ-1")).labels == before


async def test_inbox_wait_ignores_events_from_before_the_probe_started():
    inbox = EventInbox()
    await inbox.push(event("old", key="SBX-1"))
    assert await inbox.wait_for(lambda e: e.entity_key == "SBX-1", 0.05, start=1) is None
    assert (await inbox.wait_for(lambda e: e.entity_key == "SBX-1", 0.05)) is not None


def test_access_policy_default_cannot_run_probes():
    assert Access.WRITE not in AccessPolicy().allowed
