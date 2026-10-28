"""DM-46: Confluence Data Center webhooks (ConfluenceDCWebhookProvisioner, ConfluenceDCWebhookReceiver, verify_dm_46)."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from atlian_assiant.capabilities import Product
from atlian_assiant.core.errors import InvalidRequestError
from atlian_assiant.events.catalog import Ingress, method
from atlian_assiant.events.model import ChangeAction, EntityType, EventSourceKind
from atlian_assiant.events.pipeline import EventPipeline
from atlian_assiant.events.provision.base import EventProvisioner, ProvisionMode
from atlian_assiant.events.provision.dc_webhooks import ConfluenceDCWebhookProvisioner, DcWebhookClient
from atlian_assiant.events.receivers import HmacSignatureVerifier, InboundRequest
from atlian_assiant.events.receivers.dc import ConfluenceDCWebhookReceiver
from atlian_assiant.events.sources import LocalQueueSource
from atlian_assiant.events.stores import MemoryDedupStore, MemoryLocalQueue
from atlian_assiant.events.verify import EventInbox, verify_dm_46
from atlian_assiant.policy import CallbackConfirmation, DenyAll

from .dcsim import dc_http

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
URL = "https://hooks.corp.example/hooks/confluence-dc"
APPROVE = CallbackConfirmation(lambda plan: True)
BODY = json.dumps({"event": "page_updated", "timestamp": 1788264000000, "userKey": "u1", "page": {"id": 4455, "title": "Runbook", "spaceKey": "ENG", "version": 7}}).encode()


def api():
    http, state = dc_http("confluence")
    return DcWebhookClient(http, "https://confluence.corp.example", "pat-123", product="confluence"), state


def test_dm_46_catalog_entry():
    m = method("DM-46")
    assert m.ingress is Ingress.LAN_ONLY and m.consumer.endswith("ConfluenceDCWebhookReceiver") and isinstance(ConfluenceDCWebhookProvisioner(None, callback_url=URL, events=[]), EventProvisioner)


async def test_dm_46_provision_creates_the_webhook_through_the_rest_api_and_is_idempotent():
    client, state = api()
    prov = ConfluenceDCWebhookProvisioner(client, callback_url=URL, events=["page_created", "page_updated"], secret="s3cret")
    plan = await prov.plan()
    assert plan.method_id == "DM-46" and plan.outputs["mode"] == "api"
    out = await prov.apply(plan, confirm=APPROVE)
    assert out["webhook_id"] == "1" and state["hooks"][0]["events"] == ["page_created", "page_updated"] and state["hooks"][0]["secret"] == "s3cret"
    assert all(r[2] == "Bearer pat-123" for r in state["requests"])
    again = ConfluenceDCWebhookProvisioner(client, callback_url=URL, events=["page_removed"])
    out2 = await again.apply(await again.plan(), confirm=APPROVE)
    assert len(state["hooks"]) == 1 and state["hooks"][0]["events"] == ["page_removed"] and out2["webhook_id"] == "1"
    await again.teardown(confirm=APPROVE)
    assert state["hooks"] == []


async def test_dm_46_provision_declined_changes_nothing_and_bad_urls_are_rejected_in_the_plan():
    client, state = api()
    prov = ConfluenceDCWebhookProvisioner(client, callback_url=URL, events=["page_created"])
    with pytest.raises(Exception, match="declined"):
        await prov.apply(await prov.plan(), confirm=DenyAll())
    assert state["hooks"] == []
    with pytest.raises(InvalidRequestError, match="http"):
        await ConfluenceDCWebhookProvisioner(client, callback_url="ftp://x", events=[]).plan()
    lan = await ConfluenceDCWebhookProvisioner(client, callback_url="http://hooks.corp.example/x", events=[]).plan()
    assert any("plain HTTP" in w for w in lan.warnings)
    guided = ConfluenceDCWebhookProvisioner(None, callback_url=URL, events=["page_created"])
    assert "Confluence admin > Webhooks" in (await guided.apply(await guided.plan(), confirm=APPROVE))["instructions"] and (await guided.plan()).outputs["mode"] == ProvisionMode.GUIDED.value


async def test_dm_46_consumer_normalizes_confluence_dc_payloads_and_dedups_on_the_delivery_id_or_body_hash():
    q = MemoryLocalQueue()
    ep = ConfluenceDCWebhookReceiver(q, verifiers=[HmacSignatureVerifier("s3cret")])
    import hashlib
    import hmac
    sig = "sha256=" + hmac.new(b"s3cret", BODY, hashlib.sha256).hexdigest()
    req = lambda h: InboundRequest("POST", "/hooks/confluence-dc", h, BODY)  # noqa: E731
    assert [(await ep.handle(req({"X-Hub-Signature": sig, "X-Request-Id": "d1"}))).status, (await ep.handle(req({"X-Hub-Signature": sig, "X-Request-Id": "d1"}))).status,
            (await ep.handle(req({}))).status] == [202, 200, 401]
    ev = (await q.get())[1]
    assert (ev.event_id, ev.product, ev.source, ev.entity_type, ev.action, ev.entity_id, ev.container_key, ev.entity_version, ev.actor_account_id) == (
        "d1", Product.CONFLUENCE, EventSourceKind.CONFLUENCE_DC_WEBHOOK, EntityType.PAGE, ChangeAction.UPDATED, "4455", "ENG", "7", "u1")
    plain = ConfluenceDCWebhookReceiver(MemoryLocalQueue())
    assert [(await plain.handle(InboundRequest("POST", "/hooks/confluence-dc", {}, BODY))).status for _ in range(2)] == [202, 200]       # body hash dedup


async def test_dm_46_space_blog_and_unknown_events_are_mapped_or_kept():
    q = MemoryLocalQueue()
    ep = ConfluenceDCWebhookReceiver(q)
    for payload in ({"event": "space_updated", "space": {"key": "ENG", "id": 9}}, {"event": "blog_created", "blog": {"id": 5, "spaceKey": "ENG"}}, {"event": "brand_new_event"}):
        await ep.handle(InboundRequest("POST", "/hooks/confluence-dc", {}, json.dumps(payload).encode()))
    evs = [(await q.get())[1] for _ in range(3)]
    assert [(e.entity_type, e.container_key) for e in evs[:2]] == [(EntityType.SPACE, "ENG"), (EntityType.BLOGPOST, "ENG")] and evs[2].entity_type is EntityType.UNKNOWN


async def test_dm_46_receiver_requires_no_public_ingress_and_events_flow_through_the_pipeline():
    q = MemoryLocalQueue()
    ep = ConfluenceDCWebhookReceiver(q)
    assert ep.requires_public_ingress is False
    await ep.handle(InboundRequest("POST", "/hooks/confluence-dc", {"X-Request-Id": "d9"}, BODY))
    src = LocalQueueSource(q, poll_interval=0.001, kind=EventSourceKind.CONFLUENCE_DC_WEBHOOK)
    pipe, seen = EventPipeline([src], dedup=MemoryDedupStore()), []

    async def handle(ev):
        seen.append(ev.entity_id)
        await src.aclose()
    pipe.on(Product.CONFLUENCE, EntityType.PAGE)(handle)
    await pipe.run()
    assert seen == ["4455"]


async def test_dm_46_verify_uses_the_test_delivery_so_no_content_is_changed():
    client, state = api()
    prov = ConfluenceDCWebhookProvisioner(client, callback_url=URL, events=["page_created"])
    inbox = EventInbox()
    ep = ConfluenceDCWebhookReceiver(MemoryLocalQueue())
    original = client.test

    async def deliver(url):
        await original(url)
        await inbox.push(await _normalize(ep))
    client.test = deliver
    report = await verify_dm_46(provisioner=prov, inbox=inbox, timeout=timedelta(seconds=1))
    assert report.ok and report.method_id == "DM-46" and state["tests"] == [URL]
    silent = await verify_dm_46(provisioner=ConfluenceDCWebhookProvisioner(api()[0], callback_url=URL, events=[]), inbox=EventInbox(), timeout=timedelta(milliseconds=30))
    assert not silent.ok and "firewall" in silent.detail
    no_api = await verify_dm_46(provisioner=ConfluenceDCWebhookProvisioner(None, callback_url=URL, events=[]), inbox=EventInbox())
    assert not no_api.ok and "guided" in no_api.detail


async def _normalize(ep):
    return ep.normalize(InboundRequest("POST", "/hooks/confluence-dc", {"X-Request-Id": "t1"}, BODY), json.loads(BODY))
