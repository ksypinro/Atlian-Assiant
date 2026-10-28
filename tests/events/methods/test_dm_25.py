"""DM-25: Jira notification schemes (NotificationSchemeProvisioner, ImapSource, verify_dm_25)."""
from __future__ import annotations

import pytest

from atlian_assiant.capabilities import Access
from atlian_assiant.core.errors import InvalidRequestError, PolicyDeniedError
from atlian_assiant.events.catalog import method
from atlian_assiant.events.provision.base import EventProvisioner, ProvisionMode
from atlian_assiant.events.provision.jira_notifications import RECIPIENT_TYPES, NotificationSchemeProvisioner
from atlian_assiant.events.verify import verify_dm_25
from atlian_assiant.policy import CallbackConfirmation
from atlian_assiant.testing.server import ScenarioServer

from ..conftest import make_rest_client
from .humancases import check_consumer, check_security_and_dedup, check_verify
from .humansim import SITE

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)
MAPPING = {"Issue Created": ["CurrentAssignee", "Reporter", ("User", "acc-bot")], "Issue Commented": ["AllWatchers", ("Group", "jira-users")]}


def test_dm_25_catalog_entry():
    assert method("DM-25").consumer.endswith("ImapSource") and isinstance(NotificationSchemeProvisioner(None, name="s", mapping={}), EventProvisioner)
    assert {"CurrentAssignee", "Reporter", "CurrentUser", "ProjectLead", "ComponentLead", "User", "Group", "ProjectRole", "AllWatchers", "UserCustomField", "GroupCustomField"} == RECIPIENT_TYPES


def test_dm_25_provision_validates_recipient_types_and_required_parameters():
    with pytest.raises(InvalidRequestError, match="unknown recipient"):
        NotificationSchemeProvisioner(None, name="s", mapping={"Issue Created": ["Pigeon"]}).validate()
    with pytest.raises(InvalidRequestError, match="needs a value"):
        NotificationSchemeProvisioner(None, name="s", mapping={"Issue Created": ["User"]}).validate()


async def test_dm_25_provision_creates_the_scheme_maps_events_and_associates_the_project():
    server = ScenarioServer()
    client = make_rest_client(server)
    prov = NotificationSchemeProvisioner(client, name="Bot mail", mapping=MAPPING, project_key="PROJ")
    plan = await prov.plan()
    assert plan.method_id == "DM-25" and plan.outputs["mode"] == "api" and any("dedicated mailbox" in w for w in plan.warnings)
    out = await prov.apply(plan, confirm=APPROVE)
    post = next(r for r in server.requests if r["method"] == "POST" and r["path"].endswith("/notificationscheme"))
    events = post["json"]["notificationSchemeEvents"]
    assert events[0]["event"] == {"id": "1"} and {"notificationType": "User", "parameter": "acc-bot"} in events[0]["notifications"] and {"notificationType": "AllWatchers"} in events[1]["notifications"]
    assert out["scheme_id"] and any(r["method"] == "PUT" and r["path"].endswith("/project/PROJ") and r["json"] == {"notificationScheme": int(out["scheme_id"])} for r in server.requests)


async def test_dm_25_provision_is_idempotent_and_reports_unknown_events_with_the_available_ones():
    server = ScenarioServer()
    client = make_rest_client(server)
    first = NotificationSchemeProvisioner(client, name="Bot mail", mapping={"Issue Created": ["Reporter"]})
    await first.apply(await first.plan(), confirm=APPROVE)
    again = NotificationSchemeProvisioner(client, name="Bot mail", mapping={"Issue Created": ["CurrentAssignee"]})
    out = await again.apply(await again.plan(), confirm=APPROVE)
    assert out["reused"] == "true" and sum(r["method"] == "POST" and r["path"].endswith("/notificationscheme") for r in server.requests) == 1
    bad = NotificationSchemeProvisioner(client, name="x", mapping={"Nope": ["Reporter"]})
    with pytest.raises(InvalidRequestError, match="Available"):
        await bad.apply(await bad.plan(), confirm=APPROVE)


async def test_dm_25_provision_writes_need_manage_write_access_through_the_policy():
    read_only = make_rest_client(ScenarioServer(), access=frozenset({Access.READ, Access.SEARCH}))
    prov = NotificationSchemeProvisioner(read_only, name="x", mapping={"Issue Created": ["Reporter"]})
    with pytest.raises(PolicyDeniedError):
        await prov.apply(await prov.plan(), confirm=APPROVE)
    guided = NotificationSchemeProvisioner(None, name="x", mapping=MAPPING, project_key="PROJ")
    plan = await guided.plan(mode=ProvisionMode.GUIDED)
    out = await guided.apply(plan, confirm=APPROVE)
    assert "Notification schemes" in out["instructions"] and "CurrentAssignee" in out["instructions"]


async def test_dm_25_consumer_and_verify():
    await check_consumer("DM-25", subject="[JIRA] (PROJ-1) Issue created")
    await check_security_and_dedup("DM-25")
    await check_verify(verify_dm_25, "DM-25")
    assert SITE
