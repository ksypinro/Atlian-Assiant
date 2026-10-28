"""DM-16: Confluence Automation -> Send web request (AutomationRuleProvisioner(product="confluence"), AutomationWebRequestReceiver, verify_dm_16)."""
from __future__ import annotations

import json

import pytest

from atlian_assiant.core.errors import PolicyDeniedError
from atlian_assiant.events.automation import (
    CONFLUENCE_ENVELOPE,
    AutomationAction,
    AutomationRuleProvisioner,
    AutomationRuleTemplate,
)
from atlian_assiant.events.catalog import method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.model import ChangeAction, EntityType
from atlian_assiant.events.receivers import (
    AutomationWebRequestReceiver,
    InboundRequest,
    StaticHeaderVerifier,
    WebhookUrlPreflight,
)
from atlian_assiant.events.receivers.preflight import ProbeResult
from atlian_assiant.events.stores import MemoryLocalQueue
from atlian_assiant.events.verify import EventInbox, verify_dm_16
from atlian_assiant.policy import CallbackConfirmation

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
    base = dict(name="Page updated -> LAN", product="confluence", trigger="page_updated", action=AutomationAction.WEB_REQUEST,
                action_config={"url": URL, "header:X-Rule-Token": "tok"})
    return AutomationRuleTemplate(**{**base, **kw})


def test_dm_16_catalog_entry():
    m = method("DM-16")
    assert m.implemented and {p.value for p in m.products} == {"confluence"}


@pytest.mark.parametrize("action", [AutomationAction.SNS, AutomationAction.SSM, AutomationAction.JEC])
async def test_dm_16_provision_rejects_jira_only_actions_and_points_to_the_alternatives(action, tmp_path):
    prov = AutomationRuleProvisioner(template(action=action, action_config={}), output_dir=tmp_path, preflight=public())
    with pytest.raises(PolicyDeniedError) as exc:
        await prov.plan()
    assert "DM-16" in exc.value.hint and "Forge" in exc.value.hint


async def test_dm_16_provision_uses_the_confluence_envelope_and_page_triggers(tmp_path):
    prov = AutomationRuleProvisioner(template(), output_dir=tmp_path, preflight=public())
    plan = await prov.plan()
    assert plan.method_id == "DM-16"
    await prov.apply(plan, confirm=APPROVE)
    rule = json.loads(next(tmp_path.glob("*.json")).read_text())["rules"][0]
    assert rule["trigger"]["type"] == "confluence.page.updated" and "{{page.id}}" in rule["actions"][0]["body"] and template().payload == CONFLUENCE_ENVELOPE
    assert rule["conditions"] == []


async def test_dm_16_consumer_normalizes_page_envelopes_and_labels_them_dm_16():
    queue = MemoryLocalQueue()
    ep = AutomationWebRequestReceiver(queue, verifiers=[StaticHeaderVerifier("X-Rule-Token", "tok")], method_id="DM-16")
    body = json.dumps({**envelope("confluence", "4455", "page_updated"), "version": "7"}).encode()
    assert (await ep.handle(InboundRequest("POST", "/hooks/automation", {"X-Rule-Token": "tok"}, body))).status == 202
    item = await queue.get()
    ev = item[1]
    assert (ev.entity_type, ev.action, ev.entity_id, ev.entity_version, ev.delivery.method_id) == (EntityType.PAGE, ChangeAction.UPDATED, "4455", "7", "DM-16")


async def test_dm_16_end_to_end_verify_toggles_a_label_on_the_probe_page_only():
    ep = AutomationWebRequestReceiver(MemoryLocalQueue(), method_id="DM-16")

    def make_site(post):
        async def on_page(page_id):
            await post(envelope("confluence", page_id, "page_updated"))
        return SiteSim(on_page=on_page)
    sim = {}
    report = await run_stack(verify_dm_16, endpoint=ep, make_site=lambda p: sim.setdefault("s", make_site(p)), config=EventPlaneConfig(probe_page="9001"))
    assert report.ok and sim["s"].writes == [("page-label", "9001")]
    nothing = await verify_dm_16(client=SiteSim(), config=EventPlaneConfig(), inbox=EventInbox())
    assert not nothing.ok and "probe_page is not configured" in nothing.detail
