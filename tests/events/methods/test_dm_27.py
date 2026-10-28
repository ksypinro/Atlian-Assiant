"""DM-27: Jira watchers (WatchProvisioner(product="jira"), ImapSource, verify_dm_27)."""
from __future__ import annotations

import pytest

from atlian_assiant.capabilities import Access
from atlian_assiant.core.errors import InvalidRequestError, PolicyDeniedError
from atlian_assiant.events.catalog import method
from atlian_assiant.events.provision.base import EventProvisioner, ProvisionMode
from atlian_assiant.events.provision.jira_notifications import WatchProvisioner
from atlian_assiant.events.verify import verify_dm_27
from atlian_assiant.policy import CallbackConfirmation, DenyAll

from ..conftest import make_client
from .humancases import check_consumer, check_security_and_dedup, check_verify

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)


def test_dm_27_catalog_entry():
    assert method("DM-27").provisioner.endswith("WatchProvisioner") and isinstance(WatchProvisioner(None, product="jira"), EventProvisioner)


async def test_dm_27_provision_watches_issues_through_the_policy_gated_facade_and_teardown_unwatches():
    client = make_client()
    prov = WatchProvisioner(client, product="jira", targets=["PROJ-1", "PROJ-2"], account_id="acc-ann")
    plan = await prov.plan()
    assert plan.method_id == "DM-27" and plan.outputs["mode"] == "api"
    out = await prov.apply(plan, confirm=APPROVE)
    assert out["watching"] == "PROJ-1,PROJ-2" and len(client.seen) == 2                       # one confirmation per write, through the client's own policy
    watchers = [w.account_id for w in await client.jira.issues.watchers("PROJ-1")]
    assert "acc-ann" in watchers
    await prov.teardown(confirm=APPROVE)
    assert "acc-ann" not in [w.account_id for w in await client.jira.issues.watchers("PROJ-1")]


async def test_dm_27_provision_needs_write_access_and_declined_plans_change_nothing():
    ro = make_client(access=frozenset({Access.READ, Access.SEARCH}))
    prov = WatchProvisioner(ro, product="jira", targets=["PROJ-1"])
    with pytest.raises(PolicyDeniedError):
        await prov.apply(await prov.plan(), confirm=APPROVE)
    client = make_client()
    p2 = WatchProvisioner(client, product="jira", targets=["PROJ-1"], account_id="acc-x")
    with pytest.raises(Exception, match="declined"):
        await p2.apply(await p2.plan(), confirm=DenyAll())
    assert "acc-x" not in [w.account_id for w in await client.jira.issues.watchers("PROJ-1")]
    with pytest.raises(InvalidRequestError):
        WatchProvisioner(None, product="bitbucket")


async def test_dm_27_provision_guided_instructions():
    out = await (p := WatchProvisioner(None, product="jira", targets=["PROJ-1"])).apply(await p.plan(mode=ProvisionMode.GUIDED), confirm=APPROVE)
    assert "open each issue > Watch" in out["instructions"]


async def test_dm_27_consumer_and_verify():
    await check_consumer("DM-27", subject="[JIRA] (PROJ-1) watched issue changed")
    await check_security_and_dedup("DM-27")
    await check_verify(verify_dm_27, "DM-27")
