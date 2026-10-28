"""Automation rule templates (design 14.7): ONE template yields the rule for Atlassian AND the mapping that normalizes what arrives."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Literal

from ..core.errors import InvalidRequestError, PolicyDeniedError, UnsupportedOperationError
from ..policy import ConfirmationHandler
from .model import ChangeEvent
from .normalize import normalize_automation_envelope
from .provision.base import (
    ProvisioningPlan,
    ProvisioningStep,
    ProvisionMode,
    VerificationReport,
    confirm_plan,
    pick_mode,
)
from .receivers.preflight import WebhookUrlPreflight


class AutomationAction(StrEnum):
    WEB_REQUEST = "web_request"   # DM-15 / DM-16
    SNS = "sns"                   # DM-17 (Jira only)
    SSM = "ssm"                   # DM-18 (Jira only)
    JEC = "jec"                   # DM-19 (JSM Premium/Enterprise)
    EMAIL = "email"               # DM-20
    SLACK = "slack"               # DM-21
    TEAMS = "teams"               # DM-22
    TWILIO = "twilio"             # DM-23
    GENERIC = "generic"           # DM-24 other connectors


JIRA_ONLY_ACTIONS = frozenset({AutomationAction.SNS, AutomationAction.SSM, AutomationAction.JEC})

STANDARD_ENVELOPE = """{
  "v": 1,
  "atl_event_id": "{{issue.id}}:{{issue.updated}}:{{rule.name}}",
  "product": "jira",
  "trigger": "<trigger>",
  "entity_type": "issue",
  "entity_id": "{{issue.id}}",
  "entity_key": "{{issue.key}}",
  "version": "{{issue.updated}}",
  "actor": "{{initiator.accountId}}",
  "occurred_at": "{{now}}"
}"""

#: Confluence variant (smart value names follow Confluence Automation's page context; unverified: VER-14)
CONFLUENCE_ENVELOPE = """{
  "v": 1,
  "atl_event_id": "{{page.id}}:{{page.version.number}}:{{rule.name}}",
  "product": "confluence",
  "trigger": "<trigger>",
  "entity_type": "page",
  "entity_id": "{{page.id}}",
  "entity_key": "{{page.title}}",
  "version": "{{page.version.number}}",
  "actor": "{{initiator.accountId}}",
  "occurred_at": "{{now}}"
}"""

JIRA_TRIGGERS = frozenset({"issue_created", "issue_transitioned", "comment_added", "field_changed", "scheduled", "incoming_webhook"})
CONFLUENCE_TRIGGERS = frozenset({"page_published", "page_updated", "comment_created", "scheduled"})
_TRIGGER_TYPES = {"issue_created": "jira.issue.event.trigger:created", "issue_transitioned": "jira.issue.transition.trigger",
                  "comment_added": "jira.issue.event.trigger:commented", "field_changed": "jira.issue.field.value.changed",
                  "page_published": "confluence.page.published", "page_updated": "confluence.page.updated",
                  "comment_created": "confluence.comment.created", "scheduled": "scheduled", "incoming_webhook": "jira.incoming.webhook"}


@dataclass(frozen=True)
class AutomationRuleTemplate:
    name: str
    product: Literal["jira", "confluence"]
    trigger: str                        # issue_created | issue_transitioned | comment_added | field_changed | page_published | page_updated |
                                        # comment_created | scheduled | incoming_webhook
    action: AutomationAction
    action_config: Mapping[str, str] = field(default_factory=dict)   # url+headers | topic ARN | SSM doc | JEC action | recipients | webhook | numbers
    condition_jql: str | None = None
    payload: str = ""               # smart-value JSON; empty = the standard envelope for the product

    def __post_init__(self) -> None:
        if not self.payload:
            object.__setattr__(self, "payload", CONFLUENCE_ENVELOPE if self.product == "confluence" else STANDARD_ENVELOPE)

    def validate(self) -> None:
        """Rejects SNS/SSM/JEC for Confluence (EV section 23 trade-offs, section 27 action list) and mismatched triggers."""
        if self.product == "confluence" and self.action in JIRA_ONLY_ACTIONS:
            raise PolicyDeniedError(f"Confluence Automation has no {self.action.value.upper()} action", status=None,
                                    hint="Use a web request (DM-16) into a managed relay, or a Forge app (DM-06/07/09) instead.")
        allowed = CONFLUENCE_TRIGGERS if self.product == "confluence" else JIRA_TRIGGERS
        if self.trigger not in allowed:
            raise InvalidRequestError(f"trigger {self.trigger!r} is not available for {self.product}", hint="Choose one of: " + ", ".join(sorted(allowed)))
        if self.action is AutomationAction.WEB_REQUEST and not self.action_config.get("url"):
            raise InvalidRequestError("a web-request rule needs action_config['url']", hint="Pass the HTTPS callback URL of your receiver.")
        needs = {AutomationAction.EMAIL: "recipients", AutomationAction.SLACK: "channel", AutomationAction.TEAMS: "webhook_url", AutomationAction.TWILIO: "to",
                 AutomationAction.GENERIC: "connector"}.get(self.action)
        if needs and not self.action_config.get(needs):
            raise InvalidRequestError(f"a {self.action.value} rule needs action_config[{needs!r}]", hint=f"Pass {needs}: " + {
                "recipients": "comma-separated addresses (or smart values such as {{issue.assignee.emailAddress}})", "channel": "the Slack channel name",
                "webhook_url": "the Teams incoming-webhook / Workflows URL", "to": "the destination phone numbers", "connector": "the connector name in Automation"}[needs])
        if self.condition_jql and self.product == "confluence":
            raise InvalidRequestError("Confluence rules do not take a JQL condition", hint="Remove condition_jql.")

    def message(self) -> str:
        """Human-readable body for email/Slack/Teams/SMS actions: always carries the key, the link and the trigger so the link parser can resolve the entity."""
        if self.product == "confluence":
            return f"{{{{page.title}}}} {{{{page.url}}}} ({self.trigger})"
        return f"{{{{issue.key}}}}: {{{{issue.summary}}}} {{{{issue.url}}}} ({self.trigger})"

    def envelope(self) -> str:
        """The payload with the trigger name filled in."""
        return self.payload.replace("<trigger>", self.trigger)

    def mapping(self) -> PayloadMapping:
        """The consumer-side mapping for this template (what arrives -> ChangeEvent)."""
        return PayloadMapping(self.product, self.trigger, self.action)

    def to_rule(self) -> dict[str, Any]:
        """Rule definition in a best-effort Automation import shape. The exact export/import format is a VERIFY item (VER-14)."""
        self.validate()
        action: dict[str, Any] = {"type": f"action.{self.action.value}", "config": dict(self.action_config)}
        if self.action not in (AutomationAction.WEB_REQUEST, AutomationAction.SNS, AutomationAction.SSM, AutomationAction.JEC):
            action["message"] = self.message()
        if self.action is AutomationAction.WEB_REQUEST:
            headers = [{"name": k, "value": v} for k, v in self.action_config.items() if k.startswith("header:")]
            action = {"type": "action.web_request", "url": self.action_config["url"], "method": "POST", "headers": [
                {"name": "Content-Type", "value": "application/json"}, *({"name": h["name"].removeprefix("header:"), "value": h["value"]} for h in headers)],
                "body": self.envelope(), "wait": False}
        rule: dict[str, Any] = {"name": self.name, "state": "ENABLED", "product": self.product,
                                "trigger": {"type": _TRIGGER_TYPES[self.trigger]}, "conditions": [], "actions": [action],
                                "x-atlian-assiant": {"format": "best-effort, unverified (VER-14)", "trigger": self.trigger}}
        if self.condition_jql:
            rule["conditions"].append({"type": "jira.jql.condition", "jql": self.condition_jql})
        return rule


@dataclass(frozen=True)
class PayloadMapping:
    """Consumer side of a template: turns the payload the rule posts into a ChangeEvent."""

    product: str
    trigger: str
    action: AutomationAction

    def normalize(self, payload: Mapping[str, Any], *, method_id: str, headers: Mapping[str, str] | None = None) -> ChangeEvent:
        body = json.dumps(payload, sort_keys=True).encode()
        return normalize_automation_envelope(payload, headers or {}, method_id=method_id, body=body)


_SMART = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


def render_smart_values(template: str, context: Mapping[str, Any]) -> str:
    """Offline rendering of ``{{dotted.path}}`` smart values against a recorded sample, for golden tests. Unknown paths render empty."""
    def lookup(match: re.Match[str]) -> str:
        node: Any = context
        for part in match.group(1).split("."):
            if isinstance(node, Mapping) and part in node:
                node = node[part]
            else:
                return ""
        return str(node)
    return _SMART.sub(lookup, template)


def bundle_path(directory: Path, template: AutomationRuleTemplate) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", template.name.lower()).strip("-") or "rule"
    return directory / f"automation-rule-{slug}.json"


def guided_steps(template: AutomationRuleTemplate) -> list[str]:
    """Exact field-by-field instructions for creating the rule in the Automation UI."""
    where = "Project settings > Automation" if template.product == "jira" else "Space settings > Automation"
    steps = [f"Open {where} and choose Create rule.", f"Trigger: {template.trigger.replace('_', ' ')} (rule name: {template.name!r})."]
    if template.condition_jql:
        steps.append(f"Add condition 'JQL condition' with: {template.condition_jql}")
    if template.action is AutomationAction.WEB_REQUEST:
        steps += [f"Add action 'Send web request'. Web request URL: {template.action_config.get('url')}", "HTTP method: POST. Web request body: Custom data.",
                  "Headers: Content-Type: application/json" + "".join(f"; {k.removeprefix('header:')}: <the shared secret>" for k in template.action_config
                                                                       if k.startswith("header:")),
                  "Custom data (paste exactly):\n" + template.envelope(), "Turn off 'Delay execution of subsequent actions until we've received a response'.",
                  "Name the rule and turn it on."]
    else:
        steps.append(f"Add action for {template.action.value} with: {dict(template.action_config)}; body/message: {template.envelope()}")
    return steps


class AutomationRuleProvisioner:
    """Provisions an Automation rule: import bundle (default), rule-management API (when probed), browser, or guided (design 14.7)."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-15", "DM-16", "DM-24"})
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.IMPORT_BUNDLE, ProvisionMode.API, ProvisionMode.BROWSER, ProvisionMode.GUIDED})

    def __init__(self, template: AutomationRuleTemplate, *, output_dir: Path = Path("."), preflight: WebhookUrlPreflight | None = None,
                 rule_api: Any = None, allow_browser_writes: bool = False, probe: Any = None) -> None:
        self.template, self.output_dir = template, output_dir
        self.preflight = preflight or WebhookUrlPreflight()
        self.rule_api, self.allow_browser_writes, self._probe = rule_api, allow_browser_writes, probe
        self.applied: dict[str, str] = {}

    @property
    def method_id(self) -> str:
        by_action = {AutomationAction.SNS: "DM-17", AutomationAction.SSM: "DM-18", AutomationAction.JEC: "DM-19", AutomationAction.EMAIL: "DM-20",
                     AutomationAction.SLACK: "DM-21", AutomationAction.TEAMS: "DM-22", AutomationAction.TWILIO: "DM-23", AutomationAction.GENERIC: "DM-24"}
        if self.template.trigger == "incoming_webhook":
            return "DM-24"
        if self.template.action in by_action:
            return by_action[self.template.action]
        return "DM-16" if self.template.product == "confluence" else "DM-15"

    def _supported(self) -> frozenset[ProvisionMode]:
        modes = {ProvisionMode.IMPORT_BUNDLE, ProvisionMode.GUIDED}
        if self.rule_api is not None:
            modes.add(ProvisionMode.API)                      # detected by probe (VER-15): injected only when the tenant exposes it
        if self.allow_browser_writes:
            modes.add(ProvisionMode.BROWSER)
        return frozenset(modes)

    async def plan(self, *, mode: ProvisionMode | None = None) -> ProvisioningPlan:
        self.template.validate()
        chosen = pick_mode(mode, self._supported(), (ProvisionMode.API, ProvisionMode.IMPORT_BUNDLE, ProvisionMode.GUIDED))
        warnings: list[str] = []
        outputs: dict[str, str] = {"mode": chosen.value}
        url = self.template.action_config.get("url")
        if url:
            report = await self.preflight.check(url)
            outputs["callback_url"] = url
            if not report.ok:
                warnings += [f"callback URL check failed: {p}" for p in report.problems]
        if self.template.action in (AutomationAction.EMAIL, AutomationAction.SLACK, AutomationAction.TEAMS, AutomationAction.TWILIO):
            warnings.append(f"the {self.template.action.value} credentials live inside Automation; consume the result with the matching human-channel source")
        if self.template.trigger == "incoming_webhook":
            warnings.append("after creating the rule, copy its incoming-webhook URL and secret into register_incoming_webhook(): the URL is a capability, store it as a secret")
        steps: tuple[ProvisioningStep, ...]
        if chosen is ProvisionMode.IMPORT_BUNDLE:
            path = bundle_path(self.output_dir, self.template)
            outputs["bundle"] = str(path)
            steps = (ProvisioningStep(f"Write the rule import file {path.name}", chosen, True, False, path),
                     ProvisioningStep("An admin imports it: Automation > ... > Import rules", chosen, True, True))
        elif chosen is ProvisionMode.API:
            steps = (ProvisioningStep(f"Create rule {self.template.name!r} through the Automation rule-management API", chosen, True, True),)
        elif chosen is ProvisionMode.BROWSER:
            steps = (ProvisioningStep("Import the bundle through the Automation UI (browser backend; each step is confirmed)", chosen, True, True),)
        else:
            steps = tuple(ProvisioningStep(text, chosen, True, True) for text in guided_steps(self.template))
        return ProvisioningPlan(self.method_id, steps, outputs, tuple(warnings))

    async def apply(self, plan: ProvisioningPlan, *, confirm: ConfirmationHandler) -> Mapping[str, str]:
        await confirm_plan(plan, confirm, operation_id="automation.incoming_webhook.trigger", target=self.template.name)
        if any(w.startswith("callback URL check failed") for w in plan.warnings):
            raise InvalidRequestError("the callback URL cannot receive Atlassian deliveries", hint="; ".join(w for w in plan.warnings),
                                      operation="events.provision")
        mode = ProvisionMode(plan.outputs["mode"])
        out = dict(plan.outputs)
        if mode is ProvisionMode.IMPORT_BUNDLE:
            path = Path(plan.outputs["bundle"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"rules": [self.template.to_rule()]}, indent=2), encoding="utf-8")
        elif mode is ProvisionMode.API:
            out["rule_id"] = str(await self.rule_api.create_rule(self.template.to_rule()))
        elif mode is ProvisionMode.BROWSER:
            raise UnsupportedOperationError("browser provisioning needs the browser backend (planned for P5)",
                                            hint="Use import_bundle (default) or guided mode now.")
        else:
            out["instructions"] = "\n".join(f"{i}. {s}" for i, s in enumerate(guided_steps(self.template), 1))
        self.applied = out
        return out

    def register_incoming_webhook(self, url: str, secret: str | None = None) -> None:
        """Record the incoming-webhook URL and secret of a ``trigger="incoming_webhook"`` rule so ``trigger_rule`` can fire it."""
        if not url.startswith("https://automation.atlassian.com/"):
            raise InvalidRequestError("not an Automation incoming-webhook URL", hint="Use the URL shown on the rule's 'Incoming webhook' trigger.")
        self.applied = {**self.applied, "incoming_webhook_url": url, **({"incoming_webhook_secret": secret} if secret else {})}

    async def trigger_rule(self, client: Any, *, issue_keys: Sequence[str] = (), data: Mapping[str, Any] | None = None, dry_run: bool = False) -> Any:
        """Fire the rule from Python through ``client.automation.trigger_incoming_webhook`` (WRITE access + confirmation apply)."""
        if "incoming_webhook_url" not in self.applied:
            raise InvalidRequestError("no incoming-webhook URL registered", hint="Call register_incoming_webhook(url, secret) first.")
        return await client.automation.trigger_incoming_webhook(self.applied["incoming_webhook_url"], secret=self.applied.get("incoming_webhook_secret"),
                                                                issue_keys=issue_keys, data=data, dry_run=dry_run)

    async def verify(self, *, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
        if self._probe is None:
            return VerificationReport(self.method_id, False, None, "no end-to-end probe configured: pass probe=<awaitable returning a VerificationReport>")
        report: VerificationReport = await self._probe(timeout)
        return report

    async def teardown(self, *, confirm: ConfirmationHandler) -> None:
        plan = ProvisioningPlan(self.method_id, (ProvisioningStep("Remove the generated rule artifact / rule", ProvisionMode.API, False, True),),
                                dict(self.applied))
        await confirm_plan(plan, confirm, operation_id="automation.incoming_webhook.trigger", target=self.template.name)
        if "bundle" in self.applied:
            Path(self.applied["bundle"]).unlink(missing_ok=True)
        if "rule_id" in self.applied and self.rule_api is not None:
            await self.rule_api.delete_rule(self.applied["rule_id"])
        self.applied = {}
