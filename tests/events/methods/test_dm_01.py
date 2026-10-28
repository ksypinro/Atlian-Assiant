"""DM-01: Jira webhook registered in the admin UI (guided / browser provisioning, JiraWebhookReceiver, verify_dm_01)."""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import replace
from datetime import timedelta

import pytest

from atlian_assiant.core.errors import InvalidRequestError, PolicyDeniedError, UnsupportedOperationError
from atlian_assiant.events.catalog import Ingress, MethodKind, method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.provision.base import EventProvisioner, ProvisionMode
from atlian_assiant.events.provision.jira_webhooks import JiraAdminWebhookProvisioner
from atlian_assiant.events.receivers import (
    HmacSignatureVerifier,
    InboundRequest,
    JiraWebhookReceiver,
    WebhookUrlPreflight,
)
from atlian_assiant.events.receivers.preflight import ProbeResult
from atlian_assiant.events.stores import MemoryLocalQueue
from atlian_assiant.events.verify import EventInbox, verify_dm_01
from atlian_assiant.policy import CallbackConfirmation

from ..conftest import make_client
from .sitesim import SiteSim, jira_body, run_stack

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
URL = "https://hooks.example.com/hooks/jira"
APPROVE = CallbackConfirmation(lambda plan: True)


def public() -> WebhookUrlPreflight:
    async def resolve(host, port):
        return ["8.8.8.8"]

    async def probe(url):
        return ProbeResult(True, status=401)
    return WebhookUrlPreflight(resolver=resolve, prober=probe)


def provisioner(client=None, **kw) -> JiraAdminWebhookProvisioner:
    return JiraAdminWebhookProvisioner(client or make_client(), callback_url=URL, events=["jira:issue_updated", "comment_created"], jql="project = PROJ",
                                       preflight=public(), **kw)


def test_dm_01_catalog_entry():
    m = method("DM-01")
    assert m.kind is MethodKind.MACHINE_PUSH and m.ingress is Ingress.REQUIRED and m.implemented and m.verifier.endswith("verify_dm_01")
    assert isinstance(provisioner(), EventProvisioner) and "DM-01" in JiraWebhookReceiver.method_ids


async def test_dm_01_provision_guided_lists_the_exact_values_to_enter_in_the_admin_ui():
    prov = provisioner(secret="s3cret", exclude_body=True)
    plan = await prov.plan(mode=ProvisionMode.GUIDED)
    assert plan.method_id == "DM-01" and plan.outputs["mode"] == "guided"
    assert any("VER-01" in w for w in plan.warnings)                                    # the signature header is unverified: say so
    out = await prov.apply(plan, confirm=APPROVE)
    text = out["instructions"]
    assert URL in text and "jira:issue_updated, comment_created" in text and "project = PROJ" in text and "Exclude body" in text and "System > WebHooks" in text
    assert "s3cret" not in text                                                          # the secret itself is never printed


async def test_dm_01_provision_browser_mode_needs_opt_in_and_the_browser_backend():
    with pytest.raises(PolicyDeniedError):
        await provisioner().plan(mode=ProvisionMode.BROWSER)
    client = make_client()
    client.policy = replace(client.policy, allow_browser_writes=True)
    prov = JiraAdminWebhookProvisioner(client, callback_url=URL, events=["jira:issue_updated"], preflight=public())
    plan = await prov.plan(mode=ProvisionMode.BROWSER)
    with pytest.raises(UnsupportedOperationError, match="P5"):
        await prov.apply(plan, confirm=APPROVE)


async def test_dm_01_provision_runs_the_url_preflight_and_blocks_a_private_callback():
    async def private(host, port):
        return ["192.168.1.10"]
    prov = JiraAdminWebhookProvisioner(make_client(), callback_url=URL, events=["jira:issue_updated"], preflight=WebhookUrlPreflight(resolver=private))
    plan = await prov.plan(mode=ProvisionMode.GUIDED)
    assert any("not publicly routable" in w for w in plan.warnings)
    with pytest.raises(InvalidRequestError, match="cannot receive"):
        await prov.apply(plan, confirm=APPROVE)


async def test_dm_01_consumer_accepts_signed_deliveries_and_rejects_forged_ones():
    queue = MemoryLocalQueue()
    receiver = JiraWebhookReceiver(queue, verifiers=[HmacSignatureVerifier("s3cret")], method_id="DM-01")
    raw = json.dumps(jira_body("PROJ-1")).encode()
    good = {"X-Hub-Signature": "sha256=" + hmac.new(b"s3cret", raw, hashlib.sha256).hexdigest(), "X-Atlassian-Webhook-Identifier": "w1"}
    assert (await receiver.handle(InboundRequest("POST", "/hooks/jira", good, raw))).status == 202
    assert (await receiver.handle(InboundRequest("POST", "/hooks/jira", {**good, "X-Atlassian-Webhook-Identifier": "w2", "X-Hub-Signature": "sha256=00"}, raw))).status == 401
    item = await queue.get()
    assert item is not None and item[1].delivery.method_id == "DM-01" and item[1].entity_key == "PROJ-1" and await queue.depth() == 1


async def test_dm_01_end_to_end_verify_reports_latency_when_the_webhook_arrives():
    receiver = JiraWebhookReceiver(MemoryLocalQueue(), method_id="DM-01")

    def site(post):
        async def on_issue(key):
            await post(jira_body(key))
        return SiteSim(on_issue=on_issue)
    sim = {}
    report = await run_stack(verify_dm_01, endpoint=receiver, make_site=lambda p: sim.setdefault("s", site(p)), config=EventPlaneConfig(probe_issue="SBX-7"))
    assert report.ok and report.method_id == "DM-01" and report.latency_seconds is not None and "SBX-7" in report.detail
    assert sim["s"].writes == [("property", "SBX-7"), ("property", "SBX-7")]               # set, then cleared: only the sandbox issue was touched


async def test_dm_01_end_to_end_verify_fails_clearly_when_nothing_arrives():
    report = await verify_dm_01(client=SiteSim(), config=EventPlaneConfig(probe_issue="SBX-7"), inbox=EventInbox(), timeout=timedelta(milliseconds=50))
    assert not report.ok and "no event for SBX-7" in report.detail
