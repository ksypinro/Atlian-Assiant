"""DM-29: Jira saved-filter subscriptions (FilterSubscriptionProvisioner, ImapSource digest splitting, verify_dm_29)."""
from __future__ import annotations

import pytest

from atlian_assiant.core.errors import PolicyDeniedError
from atlian_assiant.events.catalog import method
from atlian_assiant.events.provision.base import ProvisionMode
from atlian_assiant.events.provision.jira_notifications import FilterSubscriptionProvisioner
from atlian_assiant.events.verify import verify_dm_29
from atlian_assiant.policy import CallbackConfirmation
from atlian_assiant.testing.server import ScenarioServer

from ..conftest import make_rest_client
from .humancases import check_consumer, check_digest, check_security_and_dedup, check_verify
from .humansim import SITE

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)


def test_dm_29_catalog_entry():
    assert method("DM-29").provisioner.endswith("FilterSubscriptionProvisioner")


async def test_dm_29_provision_modes_include_browser_only_when_opted_in():
    p = FilterSubscriptionProvisioner(None, name="f", jql="project = PROJ")
    with pytest.raises(PolicyDeniedError):
        await p.plan(mode=ProvisionMode.BROWSER)
    opted = FilterSubscriptionProvisioner(None, name="f", jql="project = PROJ", allow_browser_writes=True)
    assert (await opted.plan(mode=ProvisionMode.BROWSER)).outputs["mode"] == "browser"


async def test_dm_29_provision_api_creates_and_shares_the_filter_but_the_subscription_stays_guided():
    server = ScenarioServer()
    prov = FilterSubscriptionProvisioner(make_rest_client(server), name="Bot digest", jql="project = PROJ AND updated >= -1d")
    plan = await prov.plan()
    assert any("VER-18" in w for w in plan.warnings)
    out = await prov.apply(plan, confirm=APPROVE)
    post = next(r for r in server.requests if r["method"] == "POST" and r["path"].endswith("/filter"))
    assert post["json"]["sharePermissions"] == [{"type": "authenticated"}] and out["filter_id"] == server.filters[-1]["id"] and out["next"] == "create the subscription in the UI"
    assert next(r for r in server.requests if r["path"].endswith("/filter/search"))["query"]["expand"] == "subscriptions,jql"


async def test_dm_29_provision_reuses_an_existing_filter_and_reports_its_subscriptions():
    server = ScenarioServer()
    server.filters.append({"id": "7", "name": "Bot digest", "subscriptions": {"items": [{"id": 1}]}})
    out = await (p := FilterSubscriptionProvisioner(make_rest_client(server), name="Bot digest", jql="x")).apply(await p.plan(), confirm=APPROVE)
    assert out["filter_id"] == "7" and out["subscriptions"] == "1" and out["next"] == "subscription exists" and not any(r["method"] == "POST" for r in server.requests)


async def test_dm_29_consumer_splits_the_digest_per_issue_and_verify():
    await check_consumer("DM-29", subject="Filter: Bot digest")
    await check_security_and_dedup("DM-29")
    await check_digest("DM-29", f"Daily digest of Bot digest\n{SITE}/browse/PROJ-1\n{SITE}/browse/PROJ-2\n{SITE}/browse/PROJ-3")
    await check_verify(verify_dm_29, "DM-29")
