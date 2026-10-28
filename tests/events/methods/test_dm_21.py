"""DM-21: Automation -> Slack (AutomationRuleProvisioner(action=SLACK), SlackSocketModeSource, verify_dm_21)."""
from __future__ import annotations

import pytest

from atlian_assiant.core.errors import InvalidRequestError
from atlian_assiant.events.automation import AutomationAction, AutomationRuleProvisioner, AutomationRuleTemplate
from atlian_assiant.events.catalog import Ingress, MethodKind, method
from atlian_assiant.events.provision.base import EventProvisioner
from atlian_assiant.events.verify import verify_dm_21
from atlian_assiant.policy import CallbackConfirmation

from .slackcases import check_consumer, check_verify

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]


def template(**kw):
    return AutomationRuleTemplate(**{**dict(name="Slack on create", product="jira", trigger="issue_created", action=AutomationAction.SLACK, action_config={"channel": "atlassian-events"}), **kw})


def test_dm_21_catalog_entry():
    m = method("DM-21")
    assert m.kind is MethodKind.HUMAN and m.ingress is Ingress.NONE and m.consumer.endswith("SlackSocketModeSource") and isinstance(AutomationRuleProvisioner(template()), EventProvisioner)


async def test_dm_21_provision_generates_a_slack_rule_and_requires_a_channel(tmp_path):
    prov = AutomationRuleProvisioner(template(), output_dir=tmp_path)
    plan = await prov.plan()
    assert plan.method_id == "DM-21"
    await prov.apply(plan, confirm=CallbackConfirmation(lambda p: True))
    import json
    rule = json.loads(next(tmp_path.glob("*.json")).read_text())["rules"][0]
    assert rule["actions"][0]["type"] == "action.slack" and rule["actions"][0]["config"] == {"channel": "atlassian-events"} and "{{issue.url}}" in rule["actions"][0]["message"]
    with pytest.raises(InvalidRequestError, match="channel"):
        await AutomationRuleProvisioner(template(action_config={}), output_dir=tmp_path).plan()


async def test_dm_21_consumer_and_verify():
    await check_consumer("DM-21")
    await check_verify(verify_dm_21, "DM-21")
