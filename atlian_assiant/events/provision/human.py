"""Shared plumbing for the human-channel provisioners (design 14.10): guided steps, probe-backed verify, nothing mutated without confirmation."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any, ClassVar

from ...policy import ConfirmationHandler
from .base import ProvisioningPlan, ProvisioningStep, ProvisionMode, VerificationReport, confirm_plan, pick_mode


class HumanProvisioner:
    """Subclasses set ``method_ids`` / ``modes`` and implement ``_steps`` and ``_apply_api`` (and optionally ``_teardown_api``)."""

    method_ids: ClassVar[frozenset[str]] = frozenset()
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.GUIDED})
    operation: ClassVar[str] = "webhook.jira.dynamic.register"        # the catalog operation the confirmation is recorded against

    def __init__(self, *, probe: Any = None) -> None:
        self._probe = probe
        self.created: dict[str, str] = {}

    # hooks ---------------------------------------------------------------
    @property
    def method_id(self) -> str:
        return sorted(self.method_ids)[0]

    def supported(self) -> frozenset[ProvisionMode]:
        return self.modes

    def preference(self) -> tuple[ProvisionMode, ...]:
        return (ProvisionMode.API, ProvisionMode.GUIDED)

    def guided_steps(self) -> list[str]:
        return []

    def api_steps(self) -> list[str]:
        return []

    def warnings(self) -> tuple[str, ...]:
        return ()

    def outputs(self) -> dict[str, str]:
        return {}

    async def _apply_api(self) -> dict[str, str]:
        return {}

    async def _teardown_api(self) -> None:
        return None

    # protocol ------------------------------------------------------------
    async def plan(self, *, mode: ProvisionMode | None = None) -> ProvisioningPlan:
        chosen = pick_mode(mode, self.supported(), self.preference())
        texts = self.api_steps() if chosen is ProvisionMode.API else self.guided_steps()
        steps = tuple(ProvisioningStep(t, chosen, chosen is ProvisionMode.GUIDED, True) for t in texts)
        return ProvisioningPlan(self.method_id, steps, {"mode": chosen.value, **self.outputs()}, self.warnings())

    async def apply(self, plan: ProvisioningPlan, *, confirm: ConfirmationHandler) -> Mapping[str, str]:
        await confirm_plan(plan, confirm, operation_id=self.operation, target=self.method_id)
        if ProvisionMode(plan.outputs["mode"]) is ProvisionMode.API:
            self.created = await self._apply_api()
            return {**plan.outputs, **self.created}
        return {**plan.outputs, "instructions": "\n".join(f"{i}. {s}" for i, s in enumerate(self.guided_steps(), 1))}

    async def verify(self, *, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
        if self._probe is None:
            return VerificationReport(self.method_id, False, None, "no end-to-end probe configured: pass probe=<awaitable returning a VerificationReport>")
        report: VerificationReport = await self._probe(timeout)
        return report

    async def teardown(self, *, confirm: ConfirmationHandler) -> None:
        if not self.created:
            return
        plan = ProvisioningPlan(self.method_id, (ProvisioningStep("Remove what this provisioner created", ProvisionMode.API, False, True),), dict(self.created))
        await confirm_plan(plan, confirm, operation_id="webhook.jira.dynamic.delete", target=self.method_id)
        await self._teardown_api()
        self.created = {}
