"""DM-33: Confluence + Slack (SlackChannelProvisioner(product="confluence"), SlackSocketModeSource, verify_dm_33)."""
from __future__ import annotations

import pytest

from atlian_assiant.events.catalog import method
from atlian_assiant.events.provision.slack import SlackChannelProvisioner
from atlian_assiant.events.verify import verify_dm_33
from atlian_assiant.policy import CallbackConfirmation

from .slackcases import check_consumer, check_verify

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]


def test_dm_33_catalog_entry():
    assert {p.value for p in method("DM-33").products} == {"confluence"}


async def test_dm_33_provision_guides_the_confluence_app_and_page_space_subscriptions():
    prov = SlackChannelProvisioner(product="confluence", subscriptions=["space ENG"], private=True)
    plan = await prov.plan()
    assert plan.method_id == "DM-33" and any("groups:history" in w for w in plan.warnings)           # private channels widen the scopes: said explicitly
    out = await prov.apply(plan, confirm=CallbackConfirmation(lambda p: True))
    assert "Confluence Cloud Slack app" in out["instructions"] and "/confluence subscribe" in out["instructions"] and "space ENG" in out["instructions"]


async def test_dm_33_consumer_and_verify():
    await check_consumer("DM-33")
    await check_verify(verify_dm_33, "DM-33", page=True)
