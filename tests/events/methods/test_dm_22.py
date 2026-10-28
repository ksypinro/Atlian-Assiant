"""DM-22: Automation -> Microsoft Teams (AutomationRuleProvisioner(action=TEAMS), TeamsChannelPollingSource, verify_dm_22)."""
from __future__ import annotations

import pytest

from atlian_assiant.core.errors import InvalidRequestError
from atlian_assiant.events.automation import AutomationAction, AutomationRuleProvisioner, AutomationRuleTemplate
from atlian_assiant.events.catalog import method
from atlian_assiant.events.verify import verify_dm_22

from .teamscases import check_consumer, check_verify

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]


def template(**kw):
    return AutomationRuleTemplate(**{**dict(name="Teams on create", product="jira", trigger="issue_created", action=AutomationAction.TEAMS,
                                            action_config={"webhook_url": "https://example.webhook.office.com/x"}), **kw})


def test_dm_22_catalog_entry():
    assert method("DM-22").consumer.endswith("TeamsChannelPollingSource")


async def test_dm_22_provision_needs_the_teams_webhook_url_and_maps_to_dm_22(tmp_path):
    plan = await AutomationRuleProvisioner(template(), output_dir=tmp_path).plan()
    assert plan.method_id == "DM-22"
    with pytest.raises(InvalidRequestError, match="webhook_url"):
        await AutomationRuleProvisioner(template(action_config={}), output_dir=tmp_path).plan()


async def test_dm_22_consumer_and_verify():
    await check_consumer("DM-22")
    await check_verify(verify_dm_22, "DM-22")
