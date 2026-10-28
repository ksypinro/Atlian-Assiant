"""Provisioner contract (design section 14.3): plan -> confirm -> apply -> verify -> teardown."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Protocol, runtime_checkable

from ... import operations as ops
from ...core.errors import ConfirmationDeclinedError, PolicyDeniedError
from ...policy import ConfirmationHandler, PlannedAction


class ProvisionMode(StrEnum):
    API = "api"                       # documented REST / cloud SDK calls
    FORGE_CLI = "forge_cli"           # scaffold + `forge register / deploy / install`
    IMPORT_BUNDLE = "import_bundle"   # generated file an admin imports (e.g. Automation rule JSON)
    BROWSER = "browser"               # UI automation through the browser backend (opt-in + confirm)
    GUIDED = "guided"                 # exact step-by-step instructions, then automatic verification


@dataclass(frozen=True)
class ProvisioningStep:
    description: str
    mode: ProvisionMode
    reversible: bool
    needs_admin: bool
    artifact: Path | None = None      # generated manifest / rule JSON / IaC / JEC config


@dataclass(frozen=True)
class ProvisioningPlan:
    method_id: str                    # "DM-17"
    steps: tuple[ProvisioningStep, ...]
    outputs: Mapping[str, str] = field(default_factory=dict)   # callback_url, topic_arn, queue_url, ...
    warnings: tuple[str, ...] = ()

    def render(self) -> str:
        """Human-readable plan, printed before every ``apply()``."""
        lines = [f"Provisioning plan for {self.method_id}:"]
        lines += [f"  {i}. [{s.mode.value}{', admin' if s.needs_admin else ''}{'' if s.reversible else ', NOT reversible'}] {s.description}"
                  for i, s in enumerate(self.steps, 1)]
        lines += [f"  ! {w}" for w in self.warnings]
        return "\n".join(lines)


@dataclass(frozen=True)
class VerificationReport:
    method_id: str
    ok: bool
    latency_seconds: float | None
    detail: str


@runtime_checkable
class EventProvisioner(Protocol):
    method_ids: ClassVar[frozenset[str]]
    modes: ClassVar[frozenset[ProvisionMode]]

    async def plan(self, *, mode: ProvisionMode | None = None) -> ProvisioningPlan: ...
    async def apply(self, plan: ProvisioningPlan, *, confirm: ConfirmationHandler) -> Mapping[str, str]: ...
    async def verify(self, *, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport: ...
    async def teardown(self, *, confirm: ConfirmationHandler) -> None: ...


async def confirm_plan(plan: ProvisioningPlan, confirm: ConfirmationHandler, *, operation_id: str, target: str) -> None:
    """Ask the human to approve a provisioning plan. Raises ConfirmationDeclinedError (nothing has been changed yet)."""
    action = PlannedAction(operation=ops.get(operation_id), target=target, summary=plan.render(),
                           arguments={"method_id": plan.method_id, "outputs": dict(plan.outputs)},
                           reversible=all(s.reversible for s in plan.steps), warnings=plan.warnings)
    if not await confirm.confirm(action):
        raise ConfirmationDeclinedError(f"provisioning of {plan.method_id} was declined", hint="Nothing was changed. Do not retry without the user's approval.")


def pick_mode(requested: ProvisionMode | None, supported: frozenset[ProvisionMode], preference: tuple[ProvisionMode, ...]) -> ProvisionMode:
    """Choose a mode: the requested one if supported, else the first supported in ``preference`` (API before GUIDED)."""
    if requested is not None:
        if requested not in supported:
            raise PolicyDeniedError(f"mode {requested.value!r} is not supported here", hint="Supported: " + ", ".join(sorted(m.value for m in supported)))
        return requested
    return next(m for m in preference if m in supported)
