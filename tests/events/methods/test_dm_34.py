"""DM-34: Jira for Microsoft Teams (TeamsChannelProvisioner, TeamsChannelPollingSource, verify_dm_34)."""
from __future__ import annotations

import pytest

from atlian_assiant.events.catalog import method
from atlian_assiant.events.provision.base import EventProvisioner
from atlian_assiant.events.provision.teams import (
    GRAPH_PERMISSION_CANDIDATES,
    TeamsChannelProvisioner,
    entra_app_guidance,
)
from atlian_assiant.events.verify import verify_dm_34
from atlian_assiant.policy import CallbackConfirmation

from .teamscases import check_consumer, check_verify

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]


def test_dm_34_catalog_entry():
    assert method("DM-34").provisioner.endswith("TeamsChannelProvisioner") and isinstance(TeamsChannelProvisioner(team_id="T", channel_id="C"), EventProvisioner)


async def test_dm_34_provision_is_guided_and_scopes_the_graph_permission_to_one_team():
    prov = TeamsChannelProvisioner(team_id="T1", channel_id="CH1", jira_projects=["PROJ"])
    plan = await prov.plan()
    assert plan.method_id == "DM-34" and plan.outputs == {"mode": "guided", "team_id": "T1", "channel_id": "CH1"} and any("VER-21" in w for w in plan.warnings)
    out = await prov.apply(plan, confirm=CallbackConfirmation(lambda p: True))
    assert "Jira Cloud for Microsoft Teams" in out["instructions"] and "ChannelMessage.Read.Group" in out["instructions"] and "team T1 / channel CH1" in out["instructions"]
    g = entra_app_guidance("T1", "CH1")
    assert g["permission_preference"][0] == GRAPH_PERMISSION_CANDIDATES[0] and "RSC" in g["permission_preference"][0] and "tenant-wide" in g["permission_preference"][1]


async def test_dm_34_consumer_and_verify():
    await check_consumer("DM-34")
    await check_verify(verify_dm_34, "DM-34")
