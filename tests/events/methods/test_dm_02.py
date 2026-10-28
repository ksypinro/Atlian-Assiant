"""DM-02: Jira admin webhook REST /rest/webhooks/1.0/webhook (API provisioning, JiraWebhookReceiver, verify_dm_02)."""
from __future__ import annotations

import pytest

from atlian_assiant.core.errors import ConfirmationDeclinedError, PolicyDeniedError
from atlian_assiant.events.catalog import method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.provision.base import ProvisionMode
from atlian_assiant.events.provision.jira_webhooks import JiraAdminWebhookProvisioner
from atlian_assiant.events.receivers import JiraWebhookReceiver, WebhookUrlPreflight
from atlian_assiant.events.receivers.preflight import ProbeResult
from atlian_assiant.events.stores import MemoryLocalQueue
from atlian_assiant.events.verify import verify_dm_02
from atlian_assiant.policy import CallbackConfirmation, DenyAll
from atlian_assiant.testing.server import ScenarioServer

from ..conftest import make_client, make_rest_client
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


def provisioner(client, **kw) -> JiraAdminWebhookProvisioner:
    return JiraAdminWebhookProvisioner(client, callback_url=URL, events=["jira:issue_created"], jql="project = PROJ", preflight=public(), **kw)


def test_dm_02_catalog_entry():
    m = method("DM-02")
    assert m.implemented and m.provisioner.endswith("JiraAdminWebhookProvisioner") and m.verifier.endswith("verify_dm_02")


async def test_dm_02_provision_api_is_preferred_and_creates_the_webhook():
    client = make_client()
    prov = provisioner(client)
    plan = await prov.plan()
    assert plan.method_id == "DM-02" and plan.outputs["mode"] == "api" and plan.outputs["method"] == "DM-02"
    out = await prov.apply(plan, confirm=APPROVE)
    assert out["webhook_id"] and [w.url for w in await client.webhooks.list_admin()] == [URL]


async def test_dm_02_provision_is_idempotent_and_updates_in_place():
    client = make_client()
    first = await provisioner(client).apply(await provisioner(client).plan(), confirm=APPROVE)
    prov = JiraAdminWebhookProvisioner(client, callback_url=URL, events=["jira:issue_updated"], preflight=public())
    second = await prov.apply(await prov.plan(), confirm=APPROVE)
    hooks = await client.webhooks.list_admin()
    assert len(hooks) == 1 and hooks[0].events == ["jira:issue_updated"] and hooks[0].id == first["webhook_id"] == second["webhook_id"]


async def test_dm_02_provision_declined_by_the_user_changes_nothing():
    client = make_client()
    prov = provisioner(client)
    with pytest.raises(ConfirmationDeclinedError):
        await prov.apply(await prov.plan(), confirm=DenyAll())
    assert await client.webhooks.list_admin() == []


async def test_dm_02_provision_needs_manage_access():
    from atlian_assiant.capabilities import Access
    client = make_client(access=frozenset({Access.READ, Access.SEARCH, Access.WRITE}))
    prov = provisioner(client)
    out = await prov.plan()
    with pytest.raises(PolicyDeniedError):
        await prov.apply(out, confirm=APPROVE)


async def test_dm_02_provision_sends_the_documented_wire_shape_and_teardown_deletes_it():
    server = ScenarioServer()
    client = make_rest_client(server)
    prov = provisioner(client, secret="s", exclude_body=True)
    out = await prov.apply(await prov.plan(), confirm=APPROVE)
    post = next(r for r in server.requests if r["method"] == "POST" and r["path"].endswith("/rest/webhooks/1.0/webhook"))
    assert post["json"] == {"name": "atlian-assiant", "url": URL, "events": ["jira:issue_created"], "excludeBody": True, "secret": "s",
                            "filters": {"issue-related-events-section": "project = PROJ"}}
    assert len(server.admin_webhooks) == 1 and out["webhook_id"]
    await prov.teardown(confirm=APPROVE)
    assert server.admin_webhooks == [] and prov.webhook_id is None


async def test_dm_02_provision_guided_and_api_report_different_ledger_ids():
    prov = provisioner(make_client())
    assert (await prov.plan(mode=ProvisionMode.GUIDED)).method_id == "DM-01" and (await prov.plan(mode=ProvisionMode.API)).method_id == "DM-02"


async def test_dm_02_consumer_and_end_to_end_verify():
    receiver = JiraWebhookReceiver(MemoryLocalQueue(), method_id="DM-02")

    def make_site(post):
        async def on_issue(key):
            await post(jira_body(key, "issue_property_set"))
        return SiteSim(on_issue=on_issue)
    report = await run_stack(verify_dm_02, endpoint=receiver, make_site=make_site, config=EventPlaneConfig(probe_issue="SBX-1"))
    assert report.ok and report.method_id == "DM-02"
