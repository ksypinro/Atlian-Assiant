"""Automation rule templates, envelope, payload mapping and the rule provisioner (design 14.7)."""
from __future__ import annotations

import json

import pytest

from atlian_assiant.core.errors import (
    ConfirmationDeclinedError,
    InvalidRequestError,
    PolicyDeniedError,
    UnsupportedOperationError,
)
from atlian_assiant.events.automation import (
    CONFLUENCE_ENVELOPE,
    JIRA_ONLY_ACTIONS,
    STANDARD_ENVELOPE,
    AutomationAction,
    AutomationRuleProvisioner,
    AutomationRuleTemplate,
    guided_steps,
    render_smart_values,
)
from atlian_assiant.events.model import ChangeAction, EntityType
from atlian_assiant.events.provision.base import ProvisionMode
from atlian_assiant.events.receivers.preflight import ProbeResult, WebhookUrlPreflight
from atlian_assiant.policy import CallbackConfirmation, DenyAll

pytestmark = [pytest.mark.layer_11, pytest.mark.anyio]

URL = "https://hooks.example.com/hooks/automation"


def template(**kw) -> AutomationRuleTemplate:
    base = dict(name="Notify on create", product="jira", trigger="issue_created", action=AutomationAction.WEB_REQUEST,
                action_config={"url": URL, "header:X-Rule-Token": "tok"})
    return AutomationRuleTemplate(**{**base, **kw})


def ok_preflight() -> WebhookUrlPreflight:
    async def resolve(host, port):
        return ["8.8.8.8"]

    async def probe(url):
        return ProbeResult(True, status=401)
    return WebhookUrlPreflight(resolver=resolve, prober=probe)


APPROVE = CallbackConfirmation(lambda plan: True)


@pytest.mark.parametrize("action", sorted(JIRA_ONLY_ACTIONS, key=str))
def test_sns_ssm_and_jec_are_rejected_for_confluence(action):
    with pytest.raises(PolicyDeniedError) as exc:
        template(product="confluence", trigger="page_updated", action=action, action_config={}).validate()
    assert "DM-16" in exc.value.hint and action.value.upper() in exc.value.message


def test_jira_may_use_sns_ssm_and_jec_and_web_requests_work_for_both_products():
    for action in JIRA_ONLY_ACTIONS:
        template(action=action, action_config={"topic": "arn"}).validate()
    template(product="confluence", trigger="page_published").validate()


def test_validation_rejects_wrong_triggers_missing_urls_and_confluence_jql():
    with pytest.raises(InvalidRequestError, match="trigger"):
        template(trigger="page_updated").validate()
    with pytest.raises(InvalidRequestError, match="trigger"):
        template(product="confluence", trigger="issue_created").validate()
    with pytest.raises(InvalidRequestError, match="url"):
        template(action_config={}).validate()
    with pytest.raises(InvalidRequestError, match="JQL"):
        template(product="confluence", trigger="page_updated", condition_jql="project = X").validate()


def test_the_envelope_defaults_per_product_and_names_the_trigger():
    assert template().payload == STANDARD_ENVELOPE and template(product="confluence", trigger="page_updated").payload == CONFLUENCE_ENVELOPE
    body = json.loads(render_smart_values(template().envelope(), {"issue": {"id": "10001", "key": "PROJ-1", "updated": "2026-09-01"},
                                                                 "rule": {"name": "Notify"}, "initiator": {"accountId": "acc-1"}, "now": "2026-09-01T12:00:00Z"}))
    assert body["trigger"] == "issue_created" and body["atl_event_id"] == "10001:2026-09-01:Notify" and body["entity_key"] == "PROJ-1" and body["actor"] == "acc-1"


def test_smart_value_rendering_golden_samples_and_unknown_paths():
    ctx = {"issue": {"key": "PROJ-9", "fields": {"summary": "Hi"}}, "a": {"b": {"c": 1}}}
    assert render_smart_values("{{issue.key}}|{{ issue.fields.summary }}|{{a.b.c}}|{{missing.x}}|{{issue.key.deeper}}", ctx) == "PROJ-9|Hi|1||"


def test_rule_definition_carries_url_headers_body_condition_and_a_verify_warning():
    rule = template(condition_jql="project = PROJ").to_rule()
    action = rule["actions"][0]
    assert action["url"] == URL and action["method"] == "POST" and {"name": "X-Rule-Token", "value": "tok"} in action["headers"]
    assert json.loads(action["body"].replace("{{", "").replace("}}", ""))["trigger"] == "issue_created"
    assert rule["conditions"] == [{"type": "jira.jql.condition", "jql": "project = PROJ"}] and "VER-14" in rule["x-atlian-assiant"]["format"]


def test_payload_mapping_normalizes_what_the_rule_posts():
    mapping = template().mapping()
    ev = mapping.normalize({"atl_event_id": "1:v:r", "product": "jira", "trigger": "issue_created", "entity_type": "issue", "entity_key": "PROJ-1"}, method_id="DM-15")
    assert (ev.entity_type, ev.action, ev.entity_key) == (EntityType.ISSUE, ChangeAction.CREATED, "PROJ-1")


async def test_import_bundle_is_the_default_and_writes_the_rule_json(tmp_path):
    prov = AutomationRuleProvisioner(template(), output_dir=tmp_path, preflight=ok_preflight())
    plan = await prov.plan()
    assert plan.outputs["mode"] == "import_bundle" and plan.method_id == "DM-15" and not plan.warnings
    out = await prov.apply(plan, confirm=APPROVE)
    bundle = tmp_path / "automation-rule-notify-on-create.json"
    assert out["bundle"] == str(bundle) and json.loads(bundle.read_text())["rules"][0]["name"] == "Notify on create"
    await prov.teardown(confirm=APPROVE)
    assert not bundle.exists()


async def test_confluence_templates_map_to_dm_16_and_incoming_webhook_triggers_to_dm_24(tmp_path):
    conf = AutomationRuleProvisioner(template(product="confluence", trigger="page_updated"), output_dir=tmp_path, preflight=ok_preflight())
    assert (await conf.plan()).method_id == "DM-16"
    inc = AutomationRuleProvisioner(template(trigger="incoming_webhook"), output_dir=tmp_path, preflight=ok_preflight())
    assert (await inc.plan()).method_id == "DM-24"


async def test_api_mode_exists_only_when_a_rule_management_api_was_detected(tmp_path):
    prov = AutomationRuleProvisioner(template(), output_dir=tmp_path, preflight=ok_preflight())
    with pytest.raises(PolicyDeniedError):
        await prov.plan(mode=ProvisionMode.API)

    class RuleApi:
        def __init__(self):
            self.created, self.deleted = [], []

        async def create_rule(self, rule):
            self.created.append(rule)
            return 77

        async def delete_rule(self, rule_id):
            self.deleted.append(rule_id)
    api = RuleApi()
    prov = AutomationRuleProvisioner(template(), output_dir=tmp_path, preflight=ok_preflight(), rule_api=api)
    plan = await prov.plan()
    assert plan.outputs["mode"] == "api"                                     # preferred when available
    out = await prov.apply(plan, confirm=APPROVE)
    assert out["rule_id"] == "77" and api.created[0]["name"] == "Notify on create"
    await prov.teardown(confirm=APPROVE)
    assert api.deleted == ["77"]


async def test_guided_mode_gives_field_by_field_instructions(tmp_path):
    prov = AutomationRuleProvisioner(template(condition_jql="project = PROJ"), output_dir=tmp_path, preflight=ok_preflight())
    plan = await prov.plan(mode=ProvisionMode.GUIDED)
    out = await prov.apply(plan, confirm=APPROVE)
    text = out["instructions"]
    assert "Send web request" in text and URL in text and "project = PROJ" in text and "X-Rule-Token" in text and '"v": 1' in text
    assert not any(tmp_path.iterdir())                                       # guided mode writes nothing
    assert guided_steps(template(product="confluence", trigger="page_updated"))[0].startswith("Open Space settings")


async def test_browser_mode_needs_opt_in_and_the_browser_backend(tmp_path):
    prov = AutomationRuleProvisioner(template(), output_dir=tmp_path, preflight=ok_preflight())
    with pytest.raises(PolicyDeniedError):
        await prov.plan(mode=ProvisionMode.BROWSER)
    opted = AutomationRuleProvisioner(template(), output_dir=tmp_path, preflight=ok_preflight(), allow_browser_writes=True)
    plan = await opted.plan(mode=ProvisionMode.BROWSER)
    with pytest.raises(UnsupportedOperationError, match="P5"):
        await opted.apply(plan, confirm=APPROVE)


async def test_a_private_callback_url_is_flagged_in_the_plan_and_blocks_apply(tmp_path):
    async def private(host, port):
        return ["10.0.0.7"]
    prov = AutomationRuleProvisioner(template(), output_dir=tmp_path, preflight=WebhookUrlPreflight(resolver=private))
    plan = await prov.plan()
    assert any("not publicly routable" in w for w in plan.warnings)
    with pytest.raises(InvalidRequestError, match="cannot receive"):
        await prov.apply(plan, confirm=APPROVE)
    assert not any(tmp_path.iterdir())


async def test_apply_changes_nothing_when_the_user_declines(tmp_path):
    prov = AutomationRuleProvisioner(template(), output_dir=tmp_path, preflight=ok_preflight())
    plan = await prov.plan()
    with pytest.raises(ConfirmationDeclinedError):
        await prov.apply(plan, confirm=DenyAll())
    assert not any(tmp_path.iterdir())


async def test_non_web_request_actions_map_to_their_own_delivery_method(tmp_path):
    prov = AutomationRuleProvisioner(template(action=AutomationAction.SNS, action_config={"topic_arn": "arn:aws:sns:x"}), output_dir=tmp_path, preflight=ok_preflight())
    plan = await prov.plan()
    assert plan.method_id == "DM-17"


async def test_verify_uses_the_probe_or_says_how_to_configure_one(tmp_path):
    from datetime import timedelta

    from atlian_assiant.events.provision.base import VerificationReport
    no_probe = AutomationRuleProvisioner(template(), output_dir=tmp_path, preflight=ok_preflight())
    assert not (await no_probe.verify()).ok

    async def probe(timeout: timedelta) -> VerificationReport:
        return VerificationReport("DM-15", True, 1.5, "ok")
    assert (await AutomationRuleProvisioner(template(), output_dir=tmp_path, preflight=ok_preflight(), probe=probe).verify()).latency_seconds == 1.5
