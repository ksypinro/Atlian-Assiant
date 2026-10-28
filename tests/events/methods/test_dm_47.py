"""DM-47: Jira Data Center webhooks (JiraDCWebhookProvisioner, JiraDCWebhookReceiver, verify_dm_47)."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from atlian_assiant.events.catalog import Ingress, method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.model import ChangeAction, EntityType, EventSourceKind
from atlian_assiant.events.provision.base import EventProvisioner
from atlian_assiant.events.provision.dc_webhooks import DcWebhookClient, JiraDCWebhookProvisioner
from atlian_assiant.events.receivers import InboundRequest
from atlian_assiant.events.receivers.dc import JiraDCWebhookReceiver
from atlian_assiant.events.stores import MemoryLocalQueue
from atlian_assiant.events.verify import verify_dm_47
from atlian_assiant.policy import CallbackConfirmation

from .dcsim import dc_http
from .sitesim import SiteSim, jira_body, run_stack

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
URL = "https://hooks.corp.example/hooks/jira-dc"
APPROVE = CallbackConfirmation(lambda plan: True)


def test_dm_47_catalog_entry():
    m = method("DM-47")
    assert m.ingress is Ingress.LAN_ONLY and m.consumer.endswith("JiraDCWebhookReceiver") and isinstance(JiraDCWebhookProvisioner(None, callback_url=URL, events=[]), EventProvisioner)


async def test_dm_47_provision_uses_the_admin_webhook_endpoint_with_the_jql_filter():
    http, state = dc_http("jira")
    client = DcWebhookClient(http, "https://jira.corp.example", "pat-1", product="jira")
    prov = JiraDCWebhookProvisioner(client, callback_url=URL, events=["jira:issue_updated"], jql="project = PROJ")
    out = await prov.apply(await prov.plan(), confirm=APPROVE)
    assert out["webhook_id"] == "1" and state["hooks"][0]["filters"] == {"issue-related-events-section": "project = PROJ"}
    assert state["requests"][0][1] == "/rest/webhooks/1.0/webhook" and state["requests"][0][2] == "Bearer pat-1"
    await prov.teardown(confirm=APPROVE)
    assert state["hooks"] == []


async def test_dm_47_consumer_normalizes_like_cloud_with_the_dc_source_and_delivery_id():
    q = MemoryLocalQueue()
    ep = JiraDCWebhookReceiver(q)
    raw = json.dumps(jira_body("PROJ-4")).encode()
    assert [(await ep.handle(InboundRequest("POST", "/hooks/jira-dc", {"X-Request-Id": "r1"}, raw))).status for _ in range(2)] == [202, 200]
    ev = (await q.get())[1]
    assert (ev.event_id, ev.source, ev.entity_type, ev.action, ev.entity_key, ev.delivery.method_id) == (
        "r1", EventSourceKind.JIRA_DC_WEBHOOK, EntityType.ISSUE, ChangeAction.UPDATED, "PROJ-4", "DM-47") and ep.requires_public_ingress is False


async def test_dm_47_without_a_delivery_id_the_payload_hash_dedups():
    ep = JiraDCWebhookReceiver(MemoryLocalQueue())
    raw = json.dumps(jira_body("PROJ-5")).encode()
    assert [(await ep.handle(InboundRequest("POST", "/hooks/jira-dc", {}, raw))).status for _ in range(2)] == [202, 200]


async def test_dm_47_end_to_end_verify_inside_the_lan():
    ep = JiraDCWebhookReceiver(MemoryLocalQueue())

    def make_site(post):
        async def on_issue(key):
            await post(jira_body(key, "issue_property_set"))
        return SiteSim(on_issue=on_issue)
    report = await run_stack(verify_dm_47, endpoint=ep, make_site=make_site, config=EventPlaneConfig(probe_issue="SBX-47"))
    assert report.ok and report.method_id == "DM-47" and timedelta(0) is not None
