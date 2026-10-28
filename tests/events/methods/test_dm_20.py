"""DM-20: Automation -> email (AutomationRuleProvisioner(action=EMAIL), ImapSource, verify_dm_20)."""
from __future__ import annotations

import json

import pytest

from atlian_assiant.core.errors import InvalidRequestError
from atlian_assiant.events.automation import AutomationAction, AutomationRuleProvisioner, AutomationRuleTemplate
from atlian_assiant.events.catalog import Ingress, MethodKind, method
from atlian_assiant.events.provision.base import EventProvisioner
from atlian_assiant.events.verify import verify_dm_20
from atlian_assiant.policy import CallbackConfirmation

from .humancases import check_consumer, check_security_and_dedup, check_verify
from .humansim import SITE

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)


def template(**kw):
    return AutomationRuleTemplate(**{**dict(name="Mail on create", product="jira", trigger="issue_created", action=AutomationAction.EMAIL, action_config={"recipients": "bot@corp.example"}), **kw})


def test_dm_20_catalog_entry():
    m = method("DM-20")
    assert m.kind is MethodKind.HUMAN and m.ingress is Ingress.NONE and m.consumer.endswith("ImapSource") and isinstance(AutomationRuleProvisioner(template()), EventProvisioner)


async def test_dm_20_provision_generates_an_email_rule_whose_body_carries_the_key_and_link(tmp_path):
    prov = AutomationRuleProvisioner(template(), output_dir=tmp_path)
    plan = await prov.plan()
    assert plan.method_id == "DM-20" and any("credentials live inside Automation" in w for w in plan.warnings)
    await prov.apply(plan, confirm=APPROVE)
    rule = json.loads(next(tmp_path.glob("*.json")).read_text())["rules"][0]
    assert rule["actions"][0]["type"] == "action.email" and "{{issue.key}}" in rule["actions"][0]["message"] and "{{issue.url}}" in rule["actions"][0]["message"]
    conf = AutomationRuleProvisioner(template(product="confluence", trigger="page_updated"), output_dir=tmp_path)
    assert "{{page.url}}" in conf.template.message()


async def test_dm_20_provision_requires_recipients_and_validates_the_trigger(tmp_path):
    with pytest.raises(InvalidRequestError, match="recipients"):
        await AutomationRuleProvisioner(template(action_config={}), output_dir=tmp_path).plan()


async def test_dm_20_consumer_reads_the_rule_mail():
    await check_consumer("DM-20", subject="Automation: PROJ-1 created")
    await check_security_and_dedup("DM-20")


async def test_dm_20_end_to_end_verify():
    await check_verify(verify_dm_20, "DM-20")
    assert SITE
