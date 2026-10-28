"""DM-24: Automation -> other connectors, and the incoming-webhook trigger (AutomationRuleProvisioner, PayloadMapping, trigger_incoming_webhook, verify_dm_24)."""
from __future__ import annotations

import pytest

from atlian_assiant.core.errors import InvalidRequestError
from atlian_assiant.events.automation import (
    AutomationAction,
    AutomationRuleProvisioner,
    AutomationRuleTemplate,
    PayloadMapping,
)
from atlian_assiant.events.catalog import Ingress, method
from atlian_assiant.events.model import ChangeAction, EntityType
from atlian_assiant.events.verify import verify_dm_24
from atlian_assiant.policy import CallbackConfirmation
from atlian_assiant.testing.server import ScenarioServer

from ..conftest import make_rest_client
from .humancases import check_verify
from .test_dm_15 import public

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)
URL = "https://automation.atlassian.com/pg/webhooks/abc-123"


def incoming(**kw):
    return AutomationRuleTemplate(**{**dict(name="Manual trigger", product="jira", trigger="incoming_webhook", action=AutomationAction.WEB_REQUEST,
                                            action_config={"url": "https://hooks.example.com/hooks/automation"}), **kw})


def test_dm_24_catalog_entry():
    m = method("DM-24")
    assert m.ingress is Ingress.REQUIRED and m.consumer.endswith("PayloadMapping") and m.provisioner.endswith("AutomationRuleProvisioner")


async def test_dm_24_provision_generic_connectors_need_a_connector_name_and_map_to_dm_24(tmp_path):
    t = AutomationRuleTemplate(name="PagerDuty", product="jira", trigger="issue_created", action=AutomationAction.GENERIC, action_config={"connector": "pagerduty"})
    assert (await AutomationRuleProvisioner(t, output_dir=tmp_path).plan()).method_id == "DM-24"
    with pytest.raises(InvalidRequestError, match="connector"):
        await AutomationRuleProvisioner(AutomationRuleTemplate(name="x", product="jira", trigger="issue_created", action=AutomationAction.GENERIC), output_dir=tmp_path).plan()


async def test_dm_24_provision_incoming_webhook_rules_record_the_url_and_secret_so_python_can_fire_them(tmp_path):
    prov = AutomationRuleProvisioner(incoming(), output_dir=tmp_path, preflight=public())
    plan = await prov.plan()
    assert plan.method_id == "DM-24" and any("incoming-webhook URL" in w for w in plan.warnings)
    await prov.apply(plan, confirm=APPROVE)
    with pytest.raises(InvalidRequestError, match="no incoming-webhook URL"):
        await prov.trigger_rule(None)
    with pytest.raises(InvalidRequestError, match="not an Automation"):
        prov.register_incoming_webhook("https://evil.example.com/x")
    prov.register_incoming_webhook(URL, "tok")
    server = ScenarioServer()
    client = make_rest_client(server)
    preview = await prov.trigger_rule(client, issue_keys=["PROJ-1"], dry_run=True)
    assert preview.operation.id == "automation.incoming_webhook.trigger" and server.automation_calls == []
    await prov.trigger_rule(client, issue_keys=["PROJ-1"], data={"why": "manual"})
    assert server.automation_calls[-1]["body"] == {"why": "manual", "issues": ["PROJ-1"]} and len(client.seen) == 1


def test_dm_24_payload_mapping_is_connector_specific_and_normalizes_the_envelope():
    mapping = incoming().mapping()
    assert isinstance(mapping, PayloadMapping) and mapping.trigger == "incoming_webhook"
    ev = mapping.normalize({"atl_event_id": "m1", "product": "jira", "trigger": "incoming_webhook", "entity_type": "issue", "entity_key": "PROJ-1"}, method_id="DM-24")
    assert (ev.entity_type, ev.action, ev.delivery.method_id) == (EntityType.ISSUE, ChangeAction.PERFORMED, "DM-24")


async def test_dm_24_verify_through_the_connectors_consumer():
    await check_verify(verify_dm_24, "DM-24")
