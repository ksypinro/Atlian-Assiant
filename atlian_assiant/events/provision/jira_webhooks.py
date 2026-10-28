"""Jira webhook provisioners: admin family (DM-01 / DM-02) and dynamic registrations (DM-03, DM-05)."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import anyio

from ...core.errors import InvalidRequestError, UnsupportedOperationError
from ...models.webhooks import WebhookRegistration, WebhookSubscription
from ...policy import ConfirmationHandler
from ..receivers.preflight import WebhookUrlPreflight
from .base import ProvisioningPlan, ProvisioningStep, ProvisionMode, VerificationReport, confirm_plan, pick_mode

DYNAMIC_EVENTS = frozenset({
    "jira:issue_created", "jira:issue_updated", "jira:issue_deleted", "comment_created", "comment_updated", "comment_deleted",
    "issue_property_set", "issue_property_deleted", "sprint_created", "sprint_updated", "sprint_closed", "sprint_deleted", "sprint_started",
    "jira:version_released", "jira:version_unreleased", "jira:version_created", "jira:version_moved", "jira:version_updated",
    "jira:version_merged", "jira:version_deleted"})
NO_JQL_EVENTS = frozenset(e for e in DYNAMIC_EVENTS if e.startswith(("sprint_", "jira:version_")))   # jqlFilter does not restrict these (EV 6.3)
OAUTH_LIMIT, CONNECT_LIMIT = 5, 100
EXPIRY_DAYS, REFRESH_EVERY = 30, timedelta(days=20)

_CLAUSE = re.compile(r"^\s*(issueKey|project|issuetype|status|priority|assignee|reporter|issue\.property\[[^\]]+\](?:\.[\w.]+)?|cf\[\d+\])\s*"
                     r"(=|!=|NOT\s+IN|IN)\s*(.+?)\s*$", re.I)


def validate_dynamic_jql(jql: str) -> list[str]:
    """Problems with a dynamic-webhook JQL filter: only the restricted subset (EV 6.3) is accepted."""
    if re.search(r"\border\s+by\b", jql, re.I):
        return ["ORDER BY is not allowed in a webhook filter"]
    problems = []
    for clause in re.split(r"\s+(?:AND|OR)\s+", jql, flags=re.I):
        clause = clause.strip("() ")
        if clause and not _CLAUSE.match(clause):
            problems.append(f"unsupported clause {clause!r}: allowed fields are issueKey, project, issuetype, status, priority, assignee, reporter, "
                            "issue.property, cf[id] with = != IN NOT IN")
    return problems


def validate_dynamic_events(events: Sequence[str]) -> list[str]:
    unknown = [e for e in events if e not in DYNAMIC_EVENTS]
    return [f"events {unknown} cannot be registered dynamically; register an admin webhook (DM-02) for them"] if unknown else []


class _Probed:
    _probe: Any = None

    async def verify(self, *, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
        method = sorted(self.method_ids)[0]    # type: ignore[attr-defined]
        if self._probe is None:
            return VerificationReport(method, False, None, "no end-to-end probe configured: pass probe=<awaitable returning a VerificationReport>")
        report: VerificationReport = await self._probe(timeout)
        return report


class JiraAdminWebhookProvisioner(_Probed):
    """DM-01 (admin UI: guided / browser) and DM-02 (admin REST: api). Always runs ``WebhookUrlPreflight`` first."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-01", "DM-02"})
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.API, ProvisionMode.GUIDED, ProvisionMode.BROWSER})

    def __init__(self, client: Any, *, callback_url: str, events: Sequence[str], name: str = "atlian-assiant", jql: str | None = None,
                 exclude_body: bool = False, secret: str | None = None, preflight: WebhookUrlPreflight | None = None,
                 probe: Any = None) -> None:
        self.client, self.callback_url, self.events, self.name = client, callback_url, list(events), name
        self.jql, self.exclude_body, self.secret = jql, exclude_body, secret
        self.preflight = preflight or WebhookUrlPreflight()
        self._probe = probe
        self.webhook_id: str | None = None

    def _supported(self) -> frozenset[ProvisionMode]:
        modes = {ProvisionMode.API, ProvisionMode.GUIDED}
        if getattr(self.client.policy, "allow_browser_writes", False):
            modes.add(ProvisionMode.BROWSER)
        return frozenset(modes)

    async def plan(self, *, mode: ProvisionMode | None = None) -> ProvisioningPlan:
        chosen = pick_mode(mode, self._supported(), (ProvisionMode.API, ProvisionMode.GUIDED))
        report = await self.preflight.check(self.callback_url)
        warnings = tuple(f"callback URL check failed: {p}" for p in report.problems)
        warnings += ("the admin webhook signature header/algorithm is unverified (VER-01): verify with the sandbox before trusting it",) if self.secret else ()
        outputs = {"mode": chosen.value, "callback_url": self.callback_url, "events": ",".join(self.events), "method": "DM-02" if chosen is ProvisionMode.API else "DM-01"}
        steps: tuple[ProvisioningStep, ...]
        if chosen is ProvisionMode.API:
            steps = (ProvisioningStep(f"Create or update the admin webhook {self.name!r} -> {self.callback_url}", chosen, True, True),)
        elif chosen is ProvisionMode.BROWSER:
            steps = (ProvisioningStep("Fill the admin-UI webhook form through the browser backend (each step confirmed)", chosen, True, True),)
        else:
            steps = tuple(ProvisioningStep(s, chosen, True, True) for s in self.guided_steps())
        return ProvisioningPlan("DM-02" if chosen is ProvisionMode.API else "DM-01", steps, outputs, warnings)

    def guided_steps(self) -> list[str]:
        steps = ["Jira settings > System > WebHooks > Create a WebHook.", f"Name: {self.name}", f"URL: {self.callback_url}",
                 "Events: " + ", ".join(self.events)]
        if self.jql:
            steps.append(f"Issue-related events JQL: {self.jql}")
        if self.exclude_body:
            steps.append("Tick 'Exclude body'.")
        if self.secret:
            steps.append("Secret: <the configured shared secret>")
        steps.append("Save, then run verify() to confirm a delivery arrives.")
        return steps

    async def apply(self, plan: ProvisioningPlan, *, confirm: ConfirmationHandler) -> Mapping[str, str]:
        await confirm_plan(plan, confirm, operation_id="webhook.jira.admin.register", target=self.callback_url)
        if plan.warnings and any(w.startswith("callback URL check failed") for w in plan.warnings):
            raise InvalidRequestError("the callback URL cannot receive Atlassian deliveries", hint="; ".join(plan.warnings), operation="events.provision")
        mode = ProvisionMode(plan.outputs["mode"])
        out = dict(plan.outputs)
        if mode is ProvisionMode.BROWSER:
            raise UnsupportedOperationError("browser provisioning needs the browser backend (planned for P5)", hint="Use api or guided mode now.")
        if mode is ProvisionMode.GUIDED:
            out["instructions"] = "\n".join(f"{i}. {s}" for i, s in enumerate(self.guided_steps(), 1))
            return out
        existing = [w for w in await self.client.webhooks.list_admin() if w.url == self.callback_url]
        if existing:                                                                    # idempotent ensure(): update in place
            reg = await self.client.webhooks.update_admin(existing[0].id, self.name, self.callback_url, self.events, jql=self.jql,
                                                         exclude_body=self.exclude_body, secret=self.secret)
        else:
            reg = await self.client.webhooks.register_admin(self.name, self.callback_url, self.events, jql=self.jql,
                                                           exclude_body=self.exclude_body, secret=self.secret)
        self.webhook_id = reg.id
        out["webhook_id"] = reg.id
        return out

    async def teardown(self, *, confirm: ConfirmationHandler) -> None:
        if self.webhook_id is None:
            return
        plan = ProvisioningPlan("DM-02", (ProvisioningStep(f"Delete admin webhook {self.webhook_id}", ProvisionMode.API, False, True),), {"webhook_id": self.webhook_id})
        await confirm_plan(plan, confirm, operation_id="webhook.jira.admin.delete", target=self.webhook_id)
        await self.client.webhooks.delete_admin(self.webhook_id)
        self.webhook_id = None


class JiraDynamicWebhookProvisioner(_Probed):
    """DM-03 / DM-05: idempotent ``ensure()`` of dynamic webhooks with client-side validation, and the 20-day refresh job."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-03", "DM-05"})
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.API})

    def __init__(self, client: Any, *, callback_url: str, subscriptions: Sequence[WebhookSubscription], secret: str | None = None,
                 limit: int = OAUTH_LIMIT, preflight: WebhookUrlPreflight | None = None, probe: Any = None,
                 clock: Any = lambda: datetime.now(UTC)) -> None:
        self.client, self.callback_url, self.subscriptions, self.secret, self.limit = client, callback_url, list(subscriptions), secret, limit
        self.preflight = preflight or WebhookUrlPreflight()
        self._probe, self._clock = probe, clock
        self.webhook_ids: list[str] = []
        self.last_refresh: datetime | None = None

    def validate(self) -> list[str]:
        problems: list[str] = []
        for sub in self.subscriptions:
            problems += validate_dynamic_events(sub.events)
            if sub.jql_filter:
                problems += validate_dynamic_jql(sub.jql_filter)
            elif NO_JQL_EVENTS & set(sub.events):
                problems.append("sprint/version events need a jqlFilter in the request even though it does not restrict them (EV 6.3)")
        return problems

    def consumer_side_filters(self) -> list[str]:
        """Events whose ``jqlFilter`` is ignored by Atlassian: the consumer must filter them itself (design 14.15)."""
        return sorted(e for sub in self.subscriptions for e in sub.events if e in NO_JQL_EVENTS and sub.jql_filter)

    async def plan(self, *, mode: ProvisionMode | None = None) -> ProvisioningPlan:
        pick_mode(mode, self.modes, (ProvisionMode.API,))
        problems = self.validate()
        if problems:
            raise InvalidRequestError("invalid dynamic webhook subscription: " + "; ".join(problems), hint="Fix the events/JQL; see EV section 6.",
                                      operation="events.provision")
        report = await self.preflight.check(self.callback_url)
        warnings = [f"callback URL check failed: {p}" for p in report.problems]
        if self.consumer_side_filters():
            warnings.append(f"jqlFilter does not restrict {self.consumer_side_filters()}: filter these events in your consumer")
        warnings.append(f"registrations expire after {EXPIRY_DAYS} days; a refresh every {REFRESH_EVERY.days} days is scheduled")
        steps = (ProvisioningStep(f"Register {len(self.subscriptions)} dynamic webhook subscription(s) -> {self.callback_url} (skips identical existing ones)",
                                  ProvisionMode.API, True, False),
                 ProvisioningStep("Enable failed-delivery replay (DM-05)", ProvisionMode.API, True, False))
        return ProvisioningPlan("DM-03", steps, {"mode": "api", "callback_url": self.callback_url, "failed_replay": "enabled"}, tuple(warnings))

    async def apply(self, plan: ProvisioningPlan, *, confirm: ConfirmationHandler) -> Mapping[str, str]:
        await confirm_plan(plan, confirm, operation_id="webhook.jira.dynamic.register", target=self.callback_url)
        if any(w.startswith("callback URL check failed") for w in plan.warnings):
            raise InvalidRequestError("the callback URL cannot receive Atlassian deliveries", hint="; ".join(plan.warnings), operation="events.provision")
        return {**plan.outputs, **await self.ensure()}

    async def ensure(self) -> dict[str, str]:
        """Register what is missing; reuse identical registrations (same events and JQL). Enforces the per-app limit."""
        existing = await self.client.webhooks.list()
        have = {(frozenset(w.events), w.jql_filter or "") for w in existing}
        missing = [s for s in self.subscriptions if (frozenset(s.events), s.jql_filter or "") not in have]
        if len(existing) + len(missing) > self.limit:
            raise InvalidRequestError(f"{len(existing)} existing + {len(missing)} new webhooks exceed the limit of {self.limit}",
                                      hint="OAuth 2.0 apps may register 5 webhooks per user per tenant, Connect apps 100. Merge subscriptions or delete old ones.",
                                      operation="events.provision")
        created: list[WebhookRegistration] = []
        if missing:
            created = await self.client.webhooks.register(self.callback_url, missing, secret=self.secret)
        matching = [w for w in existing if (frozenset(w.events), w.jql_filter or "") in {(frozenset(s.events), s.jql_filter or "") for s in self.subscriptions}]
        self.webhook_ids = [w.id for w in (*matching, *created)]
        self.last_refresh = self._clock()
        return {"webhook_ids": ",".join(self.webhook_ids), "created": str(len(created)), "reused": str(len(matching))}

    async def refresh_if_due(self) -> bool:
        """Refresh all registrations when the last refresh is >= 20 days old or one expires within 10 days. Returns True if it refreshed."""
        now = self._clock()
        due = self.last_refresh is None or now - self.last_refresh >= REFRESH_EVERY
        if not due:
            live = [w for w in await self.client.webhooks.list() if w.id in self.webhook_ids]
            due = any(w.expires_at is not None and w.expires_at - now < timedelta(days=10) for w in live)
        if due and self.webhook_ids:
            await self.client.webhooks.refresh(self.webhook_ids)
            self.last_refresh = now
        return bool(due and self.webhook_ids)

    async def run_refresh_loop(self, stop: anyio.Event, *, check_every: timedelta = timedelta(hours=6)) -> None:
        """Background job: call ``refresh_if_due`` periodically until ``stop`` is set."""
        while not stop.is_set():
            await self.refresh_if_due()
            with anyio.move_on_after(check_every.total_seconds()):
                await stop.wait()

    async def teardown(self, *, confirm: ConfirmationHandler) -> None:
        if not self.webhook_ids:
            return
        plan = ProvisioningPlan("DM-03", (ProvisioningStep(f"Delete dynamic webhooks {self.webhook_ids}", ProvisionMode.API, False, False),),
                                {"webhook_ids": ",".join(self.webhook_ids)})
        await confirm_plan(plan, confirm, operation_id="webhook.jira.dynamic.delete", target=",".join(self.webhook_ids))
        await self.client.webhooks.delete(self.webhook_ids)
        self.webhook_ids = []
