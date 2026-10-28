import pytest

from atlian_assiant.core.errors import InvalidRequestError, PermissionDeniedError
from atlian_assiant.models.webhooks import WebhookSubscription
from atlian_assiant.protocols import AdminWebhookManager, WebhookManager

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]


async def test_webhook_management_dynamic_register_list_refresh_delete(harness):
    m = harness.impl(WebhookManager)
    regs = await m.register("https://hooks.example.com/jira", [WebhookSubscription(events=["jira:issue_updated"], jql_filter="project = PROJ")], secret="s")
    assert len(regs) == 1 and regs[0].events == ["jira:issue_updated"] and regs[0].jql_filter == "project = PROJ" and regs[0].id
    listed = await m.list_webhooks()
    assert [r.id for r in listed] == [regs[0].id]
    expiry = await m.refresh([regs[0].id])
    assert expiry is not None
    await m.delete([regs[0].id])
    assert await m.list_webhooks() == []


async def test_webhook_management_failed_deliveries_are_listed(harness):
    m = harness.impl(WebhookManager)
    assert await m.list_failed() == []
    if harness.server is not None:
        harness.server.failed_webhooks.append({"id": "f1", "url": "https://hooks.example.com/jira", "body": '{"webhookEvent":"jira:issue_updated"}',
                                              "failureTime": 1788264000000})
        failed = await m.list_failed()
        assert failed[0].id == "f1" and failed[0].failure_time is not None and "issue_updated" in failed[0].body
    else:
        from atlian_assiant.models.webhooks import FailedWebhook
        harness.backend.webhooks.failed.append(FailedWebhook(id="f1", body="{}"))
        assert (await m.list_failed())[0].id == "f1"


async def test_webhook_management_admin_family_register_update_delete(harness):
    a = harness.impl(AdminWebhookManager)
    reg = await a.register_admin_webhook("sync", "https://hooks.example.com/jira", ["jira:issue_created"], jql="project = PROJ", exclude_body=True, secret="s")
    assert reg.id and reg.url == "https://hooks.example.com/jira" and reg.jql_filter == "project = PROJ"
    assert [r.id for r in await a.list_admin_webhooks()] == [reg.id]
    updated = await a.update_admin_webhook(reg.id, "sync", "https://hooks.example.com/v2", ["jira:issue_updated"])
    assert updated.url == "https://hooks.example.com/v2" and updated.events == ["jira:issue_updated"]
    await a.delete_admin_webhook(reg.id)
    assert await a.list_admin_webhooks() == []


async def test_webhook_management_wire_shapes_on_rest(harness):
    if harness.server is None:
        pytest.skip("wire-level assertion")
    m, a = harness.impl(WebhookManager), harness.impl(AdminWebhookManager)
    await m.register("https://hooks.example.com/jira", [WebhookSubscription(events=["jira:issue_updated"], jql_filter="project = PROJ", field_ids_filter=["summary"])])
    post = next(r for r in harness.server.requests if r["method"] == "POST" and r["path"].endswith("/rest/api/3/webhook"))
    assert post["json"] == {"url": "https://hooks.example.com/jira", "webhooks": [{"events": ["jira:issue_updated"], "jqlFilter": "project = PROJ",
                                                                                   "fieldIdsFilter": ["summary"]}]}
    await m.delete(["701"])
    delete = next(r for r in harness.server.requests if r["method"] == "DELETE")
    assert delete["json"] == {"webhookIds": [701]}
    await m.refresh(["701"])
    assert harness.server.requests[-1]["path"].endswith("/webhook/refresh") and harness.server.requests[-1]["json"] == {"webhookIds": [701]}
    await a.register_admin_webhook("sync", "https://hooks.example.com/jira", ["jira:issue_created"], jql="project = PROJ", exclude_body=True)
    admin = next(r for r in harness.server.requests if r["path"].endswith("/rest/webhooks/1.0/webhook") and r["method"] == "POST")
    assert admin["json"] == {"name": "sync", "url": "https://hooks.example.com/jira", "events": ["jira:issue_created"], "excludeBody": True,
                             "filters": {"issue-related-events-section": "project = PROJ"}}


async def test_webhook_management_rejected_registration_explains_why_on_rest(harness):
    if harness.server is None:
        pytest.skip("server-side validation")
    m = harness.impl(WebhookManager)
    with pytest.raises(InvalidRequestError) as exc:
        await m.register("https://hooks.example.com/jira", [WebhookSubscription(events=["bogus_event"])])
    assert "bogus_event" in exc.value.message and "JQL" in exc.value.hint


async def test_webhook_management_api_token_auth_gets_a_hint_towards_admin_webhooks(harness):
    if harness.server is None:
        pytest.skip("server-side behaviour")
    harness.server.dynamic_requires_oauth = True
    with pytest.raises(PermissionDeniedError) as exc:
        await harness.impl(WebhookManager).register("https://hooks.example.com/jira", [WebhookSubscription(events=["jira:issue_updated"])])
    assert "OAuth" in exc.value.hint and "DM-02" in exc.value.hint
