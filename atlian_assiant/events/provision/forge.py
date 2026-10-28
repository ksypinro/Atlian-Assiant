"""Forge provisioners (DM-06..10): scaffold + deploy the companion app, dynamic subscriptions, and the native/realtime verifiers."""
from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any, ClassVar

from ...backends.forge.cli import ForgeCliRunner
from ...backends.forge.generator import MAX_DYNAMIC_MODULES, ForgeAppSpec, generate
from ...core.errors import AtlassianError, InvalidRequestError, UnsupportedOperationError
from ...policy import ConfirmationHandler
from .base import ProvisioningPlan, ProvisioningStep, ProvisionMode, VerificationReport, confirm_plan, pick_mode


class ForgeAppProvisioner:
    """DM-06 (native), DM-07 (inbox), DM-09 (remote), DM-10 (realtime panel): scaffold the app, then ``forge register / deploy / install``."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-06", "DM-07", "DM-09", "DM-10"})
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.FORGE_CLI, ProvisionMode.GUIDED})

    def __init__(self, spec: ForgeAppSpec, *, directory: Path, cli: ForgeCliRunner | None = None, site: str | None = None, environment: str = "production",
                 probe: Any = None, preview_features_allowed: bool = False) -> None:
        self.spec, self.directory, self.cli, self.site, self.environment = spec, directory, cli or ForgeCliRunner(), site, environment
        self._probe, self.preview_ok = probe, preview_features_allowed

    @property
    def method_id(self) -> str:
        return {"native": "DM-06", "inbox": "DM-07", "remote": "DM-09"}[self.spec.mode] if not self.spec.realtime_panel or self.spec.mode != "native" else "DM-06"

    def _products(self) -> list[str]:
        return [{"jira": "Jira", "jira-software": "Jira", "jira-service-management": "Jira", "confluence": "Confluence"}[p] for p in self.spec.products]

    async def plan(self, *, mode: ProvisionMode | None = None) -> ProvisioningPlan:
        chosen = pick_mode(mode, self.modes, (ProvisionMode.FORGE_CLI, ProvisionMode.GUIDED) if self.cli.available() else (ProvisionMode.GUIDED,))
        self.spec.validate()
        warnings: list[str] = []
        if self.spec.mode == "inbox":
            warnings.append("the Forge App REST API is Preview and must be enabled per site by an admin (EV section 15)")
            if not self.preview_ok:
                warnings.append("preview_features_allowed is false: the inbox cannot be consumed until it is enabled")
        if self.spec.filter.ignore_self is False:
            warnings.append("ignoreSelf is off: events caused by the app itself will trigger it again (loop risk)")
        warnings.append("Forge trigger delivery can take up to ~3 minutes (EV section 8.4)")
        steps: list[ProvisioningStep] = [ProvisioningStep(f"Write the app files ({len(generate(self.spec))}) to {self.directory}", chosen, True, False, self.directory)]
        for what in ("forge register (needs a one-time `forge login`)", f"forge deploy -e {self.environment}", *[f"forge install --product {p}" for p in sorted(set(self._products()))]):
            steps.append(ProvisioningStep(what, chosen, False, True))
        return ProvisioningPlan(self.method_id, tuple(steps), {"mode": chosen.value, "directory": str(self.directory), "app": self.spec.app_name}, tuple(warnings))

    def scaffold(self) -> list[Path]:
        written = []
        for rel, text in generate(self.spec).items():
            path = self.directory / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            written.append(path)
        return written

    async def apply(self, plan: ProvisioningPlan, *, confirm: ConfirmationHandler) -> Mapping[str, str]:
        await confirm_plan(plan, confirm, operation_id="webhook.jira.dynamic.register", target=self.spec.app_name)
        files = self.scaffold()
        out = {**plan.outputs, "files": str(len(files))}
        if ProvisionMode(plan.outputs["mode"]) is ProvisionMode.GUIDED:
            out["instructions"] = "\n".join(f"{i}. {s.description}" for i, s in enumerate(plan.steps[1:], 1))
            return out
        if not self.site:
            raise InvalidRequestError("site is required to install the app", hint="Pass site='example.atlassian.net'.", operation="events.provision")
        self.cli.register(self.directory, self.spec.app_name)
        self.cli.deploy(self.directory, self.environment)
        for product in sorted(set(self._products())):
            self.cli.install(self.directory, site=self.site, product=product, environment=self.environment)
        out["installed"] = ",".join(sorted(set(self._products())))
        return out

    async def verify(self, *, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
        if self._probe is None:
            return VerificationReport(self.method_id, False, None, "no end-to-end probe configured: pass probe=<awaitable returning a VerificationReport>")
        report: VerificationReport = await self._probe(timeout)
        return report

    async def teardown(self, *, confirm: ConfirmationHandler) -> None:
        plan = ProvisioningPlan(self.method_id, (ProvisioningStep("Uninstall the app from the site (forge uninstall), outside this client", ProvisionMode.GUIDED, False, True),), {})
        await confirm_plan(plan, confirm, operation_id="webhook.jira.dynamic.delete", target=self.spec.app_name)


class ForgeSubscriptionManager:
    """DM-08: dynamic trigger modules at runtime through the inbox app's ``/subscriptions`` (as the app, so no Remote is needed). Max 100 per installation."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-08"})
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.API})

    def __init__(self, app_rest: Any, *, events: list[str], key: str = "dynamic-events", function: str = "on-event", probe: Any = None) -> None:
        self.app_rest, self.events, self.key, self.function, self._probe = app_rest, events, key, function, probe

    @staticmethod
    def module(key: str, events: list[str], function: str) -> dict[str, Any]:
        return {"key": key, "function": function, "events": events, "filter": {"ignoreSelf": True}}

    async def plan(self, *, mode: ProvisionMode | None = None) -> ProvisioningPlan:
        pick_mode(mode, self.modes, (ProvisionMode.API,))
        steps = (ProvisioningStep(f"Add dynamic trigger module {self.key!r} for {len(self.events)} event(s) through the inbox /subscriptions", ProvisionMode.API, True, False),)
        return ProvisioningPlan("DM-08", steps, {"mode": "api", "key": self.key}, ("Dynamic Modules API is Preview (VER-07)", f"at most {MAX_DYNAMIC_MODULES} per installation"))

    async def apply(self, plan: ProvisioningPlan, *, confirm: ConfirmationHandler) -> Mapping[str, str]:
        await confirm_plan(plan, confirm, operation_id="webhook.jira.dynamic.register", target=self.key)
        current = await self.app_rest.list_subscriptions()
        if any(m.get("key") == self.key for m in current):                                   # idempotent: replace in place
            await self.app_rest.remove_subscription(self.key)
        elif len(current) >= MAX_DYNAMIC_MODULES:
            raise InvalidRequestError(f"{len(current)} dynamic modules already exist (maximum {MAX_DYNAMIC_MODULES})", hint="Remove unused subscriptions first.",
                                      operation="events.provision")
        await self.app_rest.add_subscription(self.module(self.key, self.events, self.function))
        return {**plan.outputs, "subscriptions": str(len(current) + (0 if any(m.get("key") == self.key for m in current) else 1))}

    async def verify(self, *, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
        started = time.monotonic()
        try:
            present = any(m.get("key") == self.key for m in await self.app_rest.list_subscriptions())
        except AtlassianError as exc:
            return VerificationReport("DM-08", False, None, f"cannot list subscriptions: {type(exc).__name__}: {exc.message}")
        return VerificationReport("DM-08", present, time.monotonic() - started, "subscription present" if present else f"subscription {self.key!r} not found")

    async def teardown(self, *, confirm: ConfirmationHandler) -> None:
        plan = ProvisioningPlan("DM-08", (ProvisioningStep(f"Remove dynamic trigger module {self.key!r}", ProvisionMode.API, False, False),), {"key": self.key})
        await confirm_plan(plan, confirm, operation_id="webhook.jira.dynamic.delete", target=self.key)
        await self.app_rest.remove_subscription(self.key)


class ForgeLogVerifier:
    """DM-06 consumer/verifier: native mode keeps everything inside Forge, so delivery is proven by the structured log line the generated handler prints."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-06"})

    def __init__(self, cli: ForgeCliRunner, directory: Path, *, environment: str = "production") -> None:
        self.cli, self.directory, self.environment = cli, directory, environment

    def parse(self, text: str) -> list[dict[str, Any]]:
        import json
        out = []
        for line in text.splitlines():
            if "atlian-assiant-event" in line:
                start = line.find("{")
                try:
                    out.append(json.loads(line[start:]))
                except ValueError:
                    continue
        return out

    async def verify(self, *, event_type: str | None = None, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
        started = time.monotonic()
        try:
            events = self.parse(self.cli.logs(self.directory, environment=self.environment, limit=200))
        except AtlassianError as exc:
            return VerificationReport("DM-06", False, None, f"cannot read forge logs: {type(exc).__name__}: {exc.message}")
        hits = [e for e in events if event_type is None or e.get("eventType") == event_type]
        return VerificationReport("DM-06", bool(hits), time.monotonic() - started if hits else None,
                                  f"{len(hits)} matching event(s) in the app logs" if hits else "no handler log lines yet: trigger a change and retry (delivery can take ~3 minutes)")


class ForgeRealtimePanel:
    """DM-10 consumer/verifier: the Custom UI panel is JavaScript; the live update is asserted by opening the panel with the browser backend (P5)."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-10"})

    def __init__(self, browser: Any = None) -> None:
        self.browser = browser

    async def verify(self, **_: Any) -> VerificationReport:
        if self.browser is None:
            return VerificationReport("DM-10", False, None, "the browser backend (planned for P5) is needed to open the panel and assert a live update")
        report: VerificationReport = await self.browser.assert_live_update()
        return report

    def scaffold_only(self) -> None:
        raise UnsupportedOperationError("the realtime panel has no Python consumer", hint="Use DM-07 for backends; DM-10 is for the UI.")
