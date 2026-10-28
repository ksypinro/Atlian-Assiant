"""Organization audit provisioners: DM-11/12 (API), DM-13 (guided/browser) and DM-14 completeness (guided)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from ...core.errors import AtlassianError, InvalidRequestError, UnsupportedOperationError
from ...policy import ConfirmationHandler
from ..receivers.preflight import WebhookUrlPreflight
from ..stores.base import CheckpointStore
from .base import ProvisioningPlan, ProvisioningStep, ProvisionMode, VerificationReport, confirm_plan, pick_mode

MAX_AUDIT_WEBHOOKS = 3
AUDIT_WARNING = ("Audit completeness depends on plan entitlements and admin-configured event exclusions: absence of an event is not proof it did not happen (EV section 21).")


class OrgAuditProvisioner:
    """DM-11 / DM-12: validates org id, API key and event access, then stores the initial stream cursor."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-11", "DM-12"})
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.API})

    def __init__(self, client: Any, checkpoints: CheckpointStore, *, stream: str = "org-events-stream", probe: Any = None,
                 clock: Any = lambda: datetime.now(UTC)) -> None:
        self.client, self.checkpoints, self.stream, self._probe, self._clock = client, checkpoints, stream, probe, clock

    async def plan(self, *, mode: ProvisionMode | None = None) -> ProvisioningPlan:
        pick_mode(mode, self.modes, (ProvisionMode.API,))
        steps = (ProvisioningStep("Check the organization API key can list audit event actions", ProvisionMode.API, True, False),
                 ProvisioningStep("Store the starting stream cursor (events from now on)", ProvisionMode.API, True, False))
        return ProvisioningPlan("DM-12", steps, {"mode": "api", "stream": self.stream}, (AUDIT_WARNING,))

    async def apply(self, plan: ProvisioningPlan, *, confirm: ConfirmationHandler) -> Mapping[str, str]:
        await confirm_plan(plan, confirm, operation_id="admin.audit.stream", target="organization audit")
        try:
            actions = await self.client.admin.audit.actions()
            page = await self.client.admin.audit.stream(start=self._clock(), limit=1)
        except AtlassianError as exc:
            raise InvalidRequestError(f"cannot read the organization audit API: {type(exc).__name__}: {exc.message}",
                                      hint=exc.hint or "Set ATLASSIAN_ORG_ID and ATLASSIAN_ORG_API_KEY; create the key in admin.atlassian.com; enable ORG_ADMIN access.",
                                      operation="events.provision") from exc
        out = {**plan.outputs, "actions": str(len(actions))}
        if page.next_cursor:
            if await self.checkpoints.load(self.stream) is None:
                await self.checkpoints.save(self.stream, page.next_cursor)
            out["cursor"] = "stored"
        return out

    async def verify(self, *, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
        if self._probe is None:
            return VerificationReport("DM-12", False, None, "no cross-check probe configured: pass probe=verify_dm_12 bound to your inbox")
        report: VerificationReport = await self._probe(timeout)
        return report

    async def teardown(self, *, confirm: ConfirmationHandler) -> None:
        await self.checkpoints.save(self.stream, "")


class AuditWebhookProvisioner:
    """DM-13: organization admins create audit-log webhooks in the UI (max 3 per org, optional Authorization header). Guided or browser only."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-13"})
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.GUIDED, ProvisionMode.BROWSER})

    def __init__(self, client: Any, *, callback_url: str, authorization: str | None = None, existing: int = 0, allow_browser_writes: bool = False,
                 preflight: WebhookUrlPreflight | None = None, probe: Any = None, entitled: bool = True) -> None:
        self.client, self.callback_url, self.authorization, self.existing = client, callback_url, authorization, existing
        self.allow_browser_writes, self.preflight, self._probe, self.entitled = allow_browser_writes, preflight or WebhookUrlPreflight(), probe, entitled

    def steps(self) -> list[str]:
        out = ["admin.atlassian.com > Security > Audit log > Webhooks > Create webhook.", f"URL: {self.callback_url}"]
        if self.authorization:
            out.append("Authorization header: <the configured shared value>")
        out.append("Save, then run verify(): the newest /events item must also arrive by webhook.")
        return out

    async def plan(self, *, mode: ProvisionMode | None = None) -> ProvisioningPlan:
        supported = frozenset({ProvisionMode.GUIDED} | ({ProvisionMode.BROWSER} if self.allow_browser_writes else set()))
        chosen = pick_mode(mode, supported, (ProvisionMode.GUIDED,))
        warnings: list[str] = [AUDIT_WARNING, "audit webhooks retry only 3 times, do not guarantee ordering and have no delivery SLO: the gap reconciler is mandatory (EV section 20)"]
        if not self.entitled:
            warnings.append("the organization is not entitled to audit-log webhooks: use /events-stream (DM-12)")
        if self.existing >= MAX_AUDIT_WEBHOOKS:
            warnings.append(f"the organization already has {self.existing} audit webhooks (maximum {MAX_AUDIT_WEBHOOKS})")
        report = await self.preflight.check(self.callback_url)
        warnings += [f"callback URL check failed: {p}" for p in report.problems]
        steps = tuple(ProvisioningStep(s, chosen, True, True) for s in self.steps())
        return ProvisioningPlan("DM-13", steps, {"mode": chosen.value, "callback_url": self.callback_url}, tuple(warnings))

    async def apply(self, plan: ProvisioningPlan, *, confirm: ConfirmationHandler) -> Mapping[str, str]:
        await confirm_plan(plan, confirm, operation_id="admin.audit.list", target=self.callback_url)
        blockers = [w for w in plan.warnings if w.startswith(("callback URL check failed", "the organization already has", "the organization is not entitled"))]
        if blockers:
            raise InvalidRequestError("audit webhook cannot be set up", hint="; ".join(blockers), operation="events.provision")
        if plan.outputs["mode"] == ProvisionMode.BROWSER.value:
            raise UnsupportedOperationError("browser provisioning needs the browser backend (planned for P5)", hint="Use guided mode now.")
        return {**plan.outputs, "instructions": "\n".join(f"{i}. {s}" for i, s in enumerate(self.steps(), 1))}

    async def verify(self, *, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
        if self._probe is None:
            return VerificationReport("DM-13", False, None, "no cross-check probe configured: pass probe=verify_dm_13 bound to your inbox")
        report: VerificationReport = await self._probe(timeout)
        return report

    async def teardown(self, *, confirm: ConfirmationHandler) -> None:
        return None            # the webhook lives in the admin UI: remove it there


@dataclass(frozen=True)
class AuditGapReport:
    excluded_categories: tuple[str, ...]
    entitlements: tuple[str, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)


class AuditCompletenessCheck:
    """DM-14: records which categories an admin confirmed as excluded and the plan entitlements; ``describe()`` reports the gaps."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-14"})
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.GUIDED})

    def __init__(self, *, excluded_categories: tuple[str, ...] = (), entitlements: tuple[str, ...] = (), confirmed_by_admin: bool = False) -> None:
        self.excluded, self.entitlements, self.confirmed = excluded_categories, entitlements, confirmed_by_admin

    def report(self) -> AuditGapReport:
        warnings = [AUDIT_WARNING]
        if not self.confirmed:
            warnings.append("exclusions have not been confirmed by an organization admin: completeness is unknown")
        warnings += [f"events in category {c!r} are excluded from the audit log" for c in self.excluded]
        if not self.entitlements:
            warnings.append("no audit entitlements recorded (Atlassian Guard features may be required)")
        return AuditGapReport(self.excluded, self.entitlements, tuple(warnings))

    async def plan(self, *, mode: ProvisionMode | None = None) -> ProvisioningPlan:
        pick_mode(mode, self.modes, (ProvisionMode.GUIDED,))
        steps = (ProvisioningStep("Open admin.atlassian.com > Security > Audit log > Event exclusions and list the excluded categories", ProvisionMode.GUIDED, True, True),
                 ProvisioningStep("Record the plan entitlements (audit log retention, webhooks)", ProvisionMode.GUIDED, True, True))
        return ProvisioningPlan("DM-14", steps, {"mode": "guided"}, tuple(self.report().warnings))

    async def apply(self, plan: ProvisioningPlan, *, confirm: ConfirmationHandler) -> Mapping[str, str]:
        await confirm_plan(plan, confirm, operation_id="admin.audit.list", target="audit completeness")
        return {"excluded": ",".join(self.excluded), "entitlements": ",".join(self.entitlements), "confirmed": str(self.confirmed).lower()}

    async def verify(self, *, timeout: timedelta = timedelta(minutes=1)) -> VerificationReport:
        r = self.report()
        return VerificationReport("DM-14", self.confirmed, None, "; ".join(r.warnings))

    async def teardown(self, *, confirm: ConfirmationHandler) -> None:
        return None
