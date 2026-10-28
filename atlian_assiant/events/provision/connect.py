"""ConnectAppProvisioner (DM-04, LEGACY): generate the Connect descriptor (webhooks module + lifecycle routes); dynamic webhooks through Connect JWT (limit 100)."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any, ClassVar

from ...core.errors import InvalidRequestError
from ...policy import ConfirmationHandler
from ..normalize import JIRA_WEBHOOK_EVENTS
from .base import ProvisioningPlan, ProvisioningStep, ProvisionMode, VerificationReport, confirm_plan, pick_mode

CONNECT_DYNAMIC_LIMIT = 100
LEGACY_WARNING = "Connect is legacy: Atlassian is moving apps to Forge. Use this only to keep or migrate an existing Connect app (EV sections 4.4, 46)."


def descriptor(*, key: str, name: str, base_url: str, events: Sequence[str], webhook_path: str = "/hooks/connect", scopes: Sequence[str] = ("READ",),
               installed: str = "/connect/installed", uninstalled: str = "/connect/uninstalled") -> dict[str, Any]:
    """``atlassian-connect.json``: JWT authentication, lifecycle routes and one ``webhooks`` module entry per event."""
    unknown = [e for e in events if e.removeprefix("jira:") not in JIRA_WEBHOOK_EVENTS]
    if unknown:
        raise InvalidRequestError(f"unknown webhook events {unknown}", hint="Use event names from the Jira webhook catalog, e.g. jira:issue_updated.")
    if not base_url.startswith("https://"):
        raise InvalidRequestError("a Connect app's baseUrl must be https", hint="Atlassian only calls HTTPS URLs; for a private receiver use a relay, tunnel or pull method.")
    return {"key": key, "name": name, "baseUrl": base_url.rstrip("/"), "authentication": {"type": "jwt"}, "lifecycle": {"installed": installed, "uninstalled": uninstalled},
            "scopes": list(scopes), "modules": {"webhooks": [{"event": e, "url": webhook_path} for e in events]}, "apiMigrations": {"signed-install": True}}


class ConnectAppProvisioner:
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-04"})
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.API, ProvisionMode.GUIDED})

    def __init__(self, *, key: str, name: str, base_url: str, events: Sequence[str], directory: Path, register_dynamic: Callable[[str, Sequence[str]], Awaitable[list[str]]] | None = None,
                 list_dynamic: Callable[[], Awaitable[int]] | None = None, probe: Any = None) -> None:
        self.key, self.name, self.base_url, self.events, self.directory = key, name, base_url, list(events), directory
        self._register, self._list, self._probe = register_dynamic, list_dynamic, probe

    async def plan(self, *, mode: ProvisionMode | None = None) -> ProvisioningPlan:
        chosen = pick_mode(mode, self.modes, (ProvisionMode.GUIDED,))
        descriptor(key=self.key, name=self.name, base_url=self.base_url, events=self.events)          # validates
        steps = (ProvisioningStep(f"Write atlassian-connect.json to {self.directory}", chosen, True, False, self.directory / "atlassian-connect.json"),
                 ProvisioningStep("Host the descriptor at baseUrl and install the app on the site (Manage apps > Upload app)", chosen, False, True),
                 ProvisioningStep("On `installed`, store the tenant's clientKey/sharedSecret ENCRYPTED; on `uninstalled`, wipe them", chosen, True, False))
        return ProvisioningPlan("DM-04", steps, {"mode": chosen.value, "descriptor": str(self.directory / "atlassian-connect.json")}, (LEGACY_WARNING,))

    async def apply(self, plan: ProvisioningPlan, *, confirm: ConfirmationHandler) -> Mapping[str, str]:
        await confirm_plan(plan, confirm, operation_id="webhook.jira.dynamic.register", target=self.key)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / "atlassian-connect.json"
        path.write_text(json.dumps(descriptor(key=self.key, name=self.name, base_url=self.base_url, events=self.events), indent=2), encoding="utf-8")
        return {**plan.outputs, "descriptor": str(path), "webhook_url": self.base_url.rstrip("/") + "/hooks/connect"}

    async def ensure_dynamic(self, events: Sequence[str]) -> list[str]:
        """Connect dynamic webhooks (registered with ``ConnectJwtAuth``): limit 100 per app per tenant, enforced before anything is sent."""
        if self._register is None or self._list is None:
            raise InvalidRequestError("no Connect-authenticated registrar configured", hint="Pass register_dynamic/list_dynamic built on a client using ConnectJwtAuth.", operation="events.provision")
        existing = await self._list()
        if existing + 1 > CONNECT_DYNAMIC_LIMIT:
            raise InvalidRequestError(f"{existing} dynamic webhooks already exist (maximum {CONNECT_DYNAMIC_LIMIT} per app per tenant)", hint="Delete unused registrations first.",
                                      operation="events.provision")
        return await self._register(self.base_url.rstrip("/") + "/hooks/connect", events)

    async def verify(self, *, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
        if self._probe is None:
            return VerificationReport("DM-04", False, None, "no end-to-end probe configured: pass probe=<awaitable returning a VerificationReport>")
        report: VerificationReport = await self._probe(timeout)
        return report

    async def teardown(self, *, confirm: ConfirmationHandler) -> None:
        plan = ProvisioningPlan("DM-04", (ProvisioningStep("Remove the generated descriptor (uninstall the app in Manage apps)", ProvisionMode.GUIDED, False, True),), {})
        await confirm_plan(plan, confirm, operation_id="webhook.jira.dynamic.delete", target=self.key)
        (self.directory / "atlassian-connect.json").unlink(missing_ok=True)
