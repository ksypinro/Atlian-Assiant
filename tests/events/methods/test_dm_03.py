"""DM-03: Jira dynamic webhook REST /rest/api/3/webhook (JiraDynamicWebhookProvisioner + refresh job, JiraWebhookReceiver, verify_dm_03)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import anyio
import pytest

from atlian_assiant.core.errors import InvalidRequestError, PermissionDeniedError
from atlian_assiant.events.catalog import method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.provision.jira_webhooks import (
    DYNAMIC_EVENTS,
    EXPIRY_DAYS,
    REFRESH_EVERY,
    JiraDynamicWebhookProvisioner,
    validate_dynamic_events,
    validate_dynamic_jql,
)
from atlian_assiant.events.receivers import JiraWebhookReceiver, WebhookUrlPreflight
from atlian_assiant.events.receivers.preflight import ProbeResult
from atlian_assiant.events.stores import MemoryLocalQueue
from atlian_assiant.events.verify import verify_dm_03
from atlian_assiant.models.webhooks import WebhookSubscription
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


def sub(*events: str, jql: str | None = "project = PROJ") -> WebhookSubscription:
    return WebhookSubscription(events=list(events), jql_filter=jql)


def provisioner(client, *subs: WebhookSubscription, **kw) -> JiraDynamicWebhookProvisioner:
    return JiraDynamicWebhookProvisioner(client, callback_url=URL, subscriptions=list(subs) or [sub("jira:issue_updated")], preflight=public(), **kw)


def test_dm_03_catalog_entry():
    m = method("DM-03")
    assert m.implemented and "DM-05" in JiraDynamicWebhookProvisioner.method_ids and m.provisioner.endswith("JiraDynamicWebhookProvisioner")


def test_dm_03_provision_validates_the_dynamic_event_subset():
    assert validate_dynamic_events(["jira:issue_created", "sprint_started", "jira:version_merged"]) == []
    problems = validate_dynamic_events(["jira:issue_updated", "worklog_created", "board_created"])
    assert len(problems) == 1 and "worklog_created" in problems[0] and "DM-02" in problems[0]
    assert len(DYNAMIC_EVENTS) == 20                                                   # exactly the EV section 6.2 list


@pytest.mark.parametrize("jql", ["project = MOBILE AND status != Done", 'issueKey IN (A-1, A-2)', "assignee = currentUser()", "priority NOT IN (Low)",
                                 "issuetype = Bug OR reporter = abc", "cf[10010] = 5", "issue.property[sync].enabled = true"])
def test_dm_03_provision_accepts_the_restricted_jql_subset(jql):
    assert validate_dynamic_jql(jql) == []


@pytest.mark.parametrize("jql", ["summary ~ foo", "updated > -1d", "project = X ORDER BY created", "labels = a", "project is EMPTY"])
def test_dm_03_provision_rejects_jql_outside_the_subset(jql):
    assert validate_dynamic_jql(jql)


async def test_dm_03_provision_plan_rejects_invalid_subscriptions_before_anything_is_sent():
    server = ScenarioServer()
    prov = provisioner(make_rest_client(server), sub("worklog_created"))
    with pytest.raises(InvalidRequestError, match="worklog_created"):
        await prov.plan()
    assert not any(r["path"].endswith("/webhook") for r in server.requests)
    with pytest.raises(InvalidRequestError, match="sprint/version"):
        await provisioner(make_client(), sub("sprint_started", jql=None)).plan()


async def test_dm_03_provision_warns_that_sprint_and_version_events_ignore_the_jql_filter():
    prov = provisioner(make_client(), sub("sprint_started", "jira:issue_updated"))
    plan = await prov.plan()
    assert prov.consumer_side_filters() == ["sprint_started"] and any("filter these events in your consumer" in w for w in plan.warnings)
    assert any(f"{EXPIRY_DAYS} days" in w and f"{REFRESH_EVERY.days} days" in w for w in plan.warnings) and plan.outputs["failed_replay"] == "enabled"


async def test_dm_03_provision_ensure_is_idempotent_and_reuses_identical_registrations():
    client = make_client()
    prov = provisioner(client, sub("jira:issue_updated"), sub("comment_created"))
    first = await prov.apply(await prov.plan(), confirm=APPROVE)
    again = provisioner(client, sub("jira:issue_updated"), sub("comment_created"))
    second = await again.apply(await again.plan(), confirm=APPROVE)
    assert first["created"] == "2" and second["created"] == "0" and second["reused"] == "2" and len(await client.webhooks.list()) == 2
    assert first["webhook_ids"] == second["webhook_ids"]


async def test_dm_03_provision_enforces_the_per_app_limit():
    client = make_client()
    prov = provisioner(client, *[sub("jira:issue_updated", jql=f"project = P{i}") for i in range(6)])
    with pytest.raises(InvalidRequestError, match="limit of 5"):
        await prov.apply(await prov.plan(), confirm=APPROVE)
    assert await client.webhooks.list() == []                                          # nothing was registered
    connect = provisioner(client, *[sub("jira:issue_updated", jql=f"project = P{i}") for i in range(6)], limit=100)
    assert (await connect.apply(await connect.plan(), confirm=APPROVE))["created"] == "6"


async def test_dm_03_provision_declined_by_the_user_registers_nothing():
    client = make_client()
    prov = provisioner(client)
    with pytest.raises(Exception) as exc:
        await prov.apply(await prov.plan(), confirm=DenyAll())
    assert "declined" in str(exc.value) and await client.webhooks.list() == []


async def test_dm_03_provision_on_api_token_auth_explains_the_oauth_requirement():
    server = ScenarioServer()
    server.dynamic_requires_oauth = True
    prov = provisioner(make_rest_client(server))
    with pytest.raises(PermissionDeniedError) as exc:
        await prov.apply(await prov.plan(), confirm=APPROVE)
    assert "OAuth" in exc.value.hint and "DM-02" in exc.value.hint


async def test_dm_03_provision_refresh_runs_every_20_days_or_when_expiry_is_near():
    clock = {"now": datetime(2026, 9, 1, tzinfo=UTC)}
    server = ScenarioServer()
    client = make_rest_client(server)
    prov = provisioner(client, clock=lambda: clock["now"])
    await prov.apply(await prov.plan(), confirm=APPROVE)
    refreshes = lambda: sum(r["path"].endswith("/webhook/refresh") for r in server.requests)  # noqa: E731
    assert not await prov.refresh_if_due() and refreshes() == 0                          # just registered; the fake expiry is far away
    clock["now"] += REFRESH_EVERY - timedelta(hours=1)
    assert not await prov.refresh_if_due()
    clock["now"] += timedelta(hours=2)
    assert await prov.refresh_if_due() and refreshes() == 1 and prov.last_refresh == clock["now"]
    server.dynamic_webhooks[0]["expirationDate"] = (clock["now"] + timedelta(days=3)).isoformat()      # expiry near: refresh early
    assert await prov.refresh_if_due() and refreshes() == 2


async def test_dm_03_provision_refresh_loop_runs_until_stopped():
    client = make_client()
    prov = provisioner(client)
    await prov.apply(await prov.plan(), confirm=APPROVE)
    prov.last_refresh = None
    stop = anyio.Event()
    async with anyio.create_task_group() as tg:
        tg.start_soon(lambda: prov.run_refresh_loop(stop, check_every=timedelta(milliseconds=5)))
        await anyio.sleep(0.05)
        stop.set()
    assert prov.last_refresh is not None


async def test_dm_03_provision_teardown_deletes_registrations():
    client = make_client()
    prov = provisioner(client)
    await prov.apply(await prov.plan(), confirm=APPROVE)
    await prov.teardown(confirm=APPROVE)
    assert await client.webhooks.list() == [] and prov.webhook_ids == []


async def test_dm_03_consumer_dedups_retries_and_end_to_end_verify_reports_latency():
    receiver = JiraWebhookReceiver(MemoryLocalQueue(), method_id="DM-03")

    def make_site(post):
        async def on_issue(key):
            await post(jira_body(key, "issue_property_set"))
        return SiteSim(on_issue=on_issue)
    report = await run_stack(verify_dm_03, endpoint=receiver, make_site=make_site, config=EventPlaneConfig(probe_issue="SBX-3"))
    assert report.ok and report.method_id == "DM-03" and report.latency_seconds is not None
