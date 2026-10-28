"""DM-15: Jira Automation -> Send web request (AutomationRuleProvisioner, AutomationWebRequestReceiver, verify_dm_15, incoming-webhook trigger)."""
from __future__ import annotations

import json

import pytest

from atlian_assiant.events.automation import AutomationAction, AutomationRuleProvisioner, AutomationRuleTemplate
from atlian_assiant.events.catalog import method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.provision.base import EventProvisioner, ProvisionMode
from atlian_assiant.events.receivers import AutomationWebRequestReceiver, StaticHeaderVerifier, WebhookUrlPreflight
from atlian_assiant.events.receivers.preflight import ProbeResult
from atlian_assiant.events.stores import MemoryLocalQueue
from atlian_assiant.events.verify import verify_dm_15
from atlian_assiant.policy import CallbackConfirmation
from atlian_assiant.testing.server import ScenarioServer

from ..conftest import make_rest_client
from .sitesim import SiteSim, envelope, run_stack

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
URL = "https://hooks.example.com/hooks/automation"
APPROVE = CallbackConfirmation(lambda plan: True)


def public() -> WebhookUrlPreflight:
    async def resolve(host, port):
        return ["8.8.8.8"]

    async def probe(url):
        return ProbeResult(True, status=401)
    return WebhookUrlPreflight(resolver=resolve, prober=probe)


def template(**kw) -> AutomationRuleTemplate:
    base = dict(name="Issue created -> LAN", product="jira", trigger="issue_created", action=AutomationAction.WEB_REQUEST,
                action_config={"url": URL, "header:X-Rule-Token": "tok"})
    return AutomationRuleTemplate(**{**base, **kw})


def test_dm_15_catalog_entry():
    m = method("DM-15")
    assert m.implemented and "DM-15" in AutomationWebRequestReceiver.method_ids and isinstance(AutomationRuleProvisioner(template()), EventProvisioner)


async def test_dm_15_provision_generates_the_import_bundle_and_the_admin_steps(tmp_path):
    prov = AutomationRuleProvisioner(template(), output_dir=tmp_path, preflight=public())
    plan = await prov.plan()
    assert plan.method_id == "DM-15" and [s.mode for s in plan.steps] == [ProvisionMode.IMPORT_BUNDLE] * 2 and plan.steps[1].needs_admin
    out = await prov.apply(plan, confirm=APPROVE)
    rule = json.loads((tmp_path / "automation-rule-issue-created-lan.json").read_text())["rules"][0]
    assert rule["actions"][0]["url"] == URL and rule["trigger"]["type"] == "jira.issue.event.trigger:created" and out["callback_url"] == URL


async def test_dm_15_consumer_dedups_on_the_envelope_id_and_rejects_requests_without_the_rule_token():
    queue = MemoryLocalQueue()
    ep = AutomationWebRequestReceiver(queue, verifiers=[StaticHeaderVerifier("X-Rule-Token", "tok")])
    body = json.dumps(envelope("jira", "PROJ-1", "issue_created")).encode()

    async def send(headers):
        from atlian_assiant.events.receivers import InboundRequest
        return (await ep.handle(InboundRequest("POST", "/hooks/automation", headers, body))).status
    assert [await send({"X-Rule-Token": "tok"}), await send({"X-Rule-Token": "tok"}), await send({}), await send({"X-Rule-Token": "nope"})] == [202, 200, 401, 401]
    item = await queue.get()
    assert item is not None and item[1].entity_key == "PROJ-1" and item[1].delivery.method_id == "DM-15" and await queue.depth() == 1


async def test_dm_15_end_to_end_verify_with_the_rule_posting_the_envelope():
    ep = AutomationWebRequestReceiver(MemoryLocalQueue(), method_id="DM-15")

    def make_site(post):
        async def on_issue(key):
            await post(envelope("jira", key, "field_changed"))
        return SiteSim(on_issue=on_issue)
    sim = {}
    report = await run_stack(verify_dm_15, endpoint=ep, make_site=lambda p: sim.setdefault("s", make_site(p)), config=EventPlaneConfig(probe_issue="SBX-2"))
    assert report.ok and report.method_id == "DM-15" and sim["s"].writes[0] == ("label", "SBX-2")


async def test_dm_15_incoming_webhook_trigger_fires_the_rule_from_python():
    server = ScenarioServer()
    client = make_rest_client(server)
    url = "https://automation.atlassian.com/pg/webhooks/abc-123"
    plan = await client.automation.trigger_incoming_webhook(url, secret="tok", issue_keys=["PROJ-1"], data={"why": "test"}, dry_run=True)
    assert plan.operation.id == "automation.incoming_webhook.trigger" and server.automation_calls == []         # a preview sends nothing
    await client.automation.trigger_incoming_webhook(url, secret="tok", issue_keys=["PROJ-1"], data={"why": "test"})
    assert server.automation_calls[-1]["body"] == {"why": "test", "issues": ["PROJ-1"]} and len(client.seen) == 1
