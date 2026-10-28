"""DM-26: Jira custom workflow event emails (CustomEventProvisioner, ImapSource, verify_dm_26)."""
from __future__ import annotations

import pytest

from atlian_assiant.core.errors import InvalidRequestError
from atlian_assiant.events.catalog import method
from atlian_assiant.events.provision.base import EventProvisioner, ProvisionMode
from atlian_assiant.events.provision.jira_notifications import CustomEventProvisioner
from atlian_assiant.events.verify import verify_dm_26
from atlian_assiant.policy import CallbackConfirmation
from atlian_assiant.testing.server import ScenarioServer

from ..conftest import make_rest_client
from .humancases import check_consumer, check_security_and_dedup, check_verify

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)


def test_dm_26_catalog_entry():
    assert method("DM-26").provisioner.endswith("CustomEventProvisioner") and isinstance(CustomEventProvisioner(None, event_name="Deployed"), EventProvisioner)


async def test_dm_26_provision_creation_is_guided_because_no_api_is_verified():
    prov = CustomEventProvisioner(None, event_name="Deployed to prod", workflow="Software workflow", transition="Release")
    plan = await prov.plan()
    assert plan.outputs["mode"] == "guided" and any("VER-19" in w for w in plan.warnings)
    out = await prov.apply(plan, confirm=APPROVE)
    text = out["instructions"]
    assert "Add event 'Deployed to prod'" in text and "Software workflow" in text and "Fire a Deployed to prod event" in text and "notification scheme (DM-25)" in text


async def test_dm_26_provision_api_mode_finds_an_existing_event_and_otherwise_explains_what_to_create():
    server = ScenarioServer()
    client = make_rest_client(server)
    found = CustomEventProvisioner(client, event_name="Issue Created")
    out = await found.apply(await found.plan(mode=ProvisionMode.API), confirm=APPROVE)
    assert out["event_id"] == "1"
    missing = CustomEventProvisioner(client, event_name="Deployed")
    with pytest.raises(InvalidRequestError) as exc:
        await missing.apply(await missing.plan(mode=ProvisionMode.API), confirm=APPROVE)
    assert "Create it in the admin UI first" in exc.value.hint


async def test_dm_26_consumer_and_verify():
    await check_consumer("DM-26", subject="[JIRA] (PROJ-1) Deployed to prod")
    await check_security_and_dedup("DM-26")
    await check_verify(verify_dm_26, "DM-26")
