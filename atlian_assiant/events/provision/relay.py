"""RelayProvisioner (DM-39..43): generate the IaC + handler for a managed relay, deploy after confirmation, output the public relay URL."""
from __future__ import annotations

import re
import shutil
from collections.abc import Callable, Mapping
from datetime import timedelta
from pathlib import Path
from typing import ClassVar, Literal

from ...core.errors import InvalidRequestError
from ...policy import ConfirmationHandler
from ...relay import iac
from ..receivers.preflight import WebhookUrlPreflight
from .base import ProvisioningPlan, ProvisioningStep, ProvisionMode, VerificationReport, confirm_plan, pick_mode

Cloud = Literal["aws", "azure", "gcp", "cloudflare", "broker"]
METHOD_BY_CLOUD = {"aws": "DM-39", "azure": "DM-40", "gcp": "DM-41", "cloudflare": "DM-42", "broker": "DM-43"}
HANDLERS = {"aws": "aws_lambda.py", "azure": "azure_function.py", "gcp": "gcp_function.py"}


class RelayProvisioner:
    method_ids: ClassVar[frozenset[str]] = frozenset(METHOD_BY_CLOUD.values())
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.API, ProvisionMode.GUIDED})

    def __init__(self, cloud: Cloud, *, directory: Path, name: str = "atlassian-events", run: Callable[[list[str], Path], str] | None = None,
                 broker_ingest_url: str | None = None, retention_seconds: int = iac.RETENTION_SECONDS, preflight: WebhookUrlPreflight | None = None, probe: object = None) -> None:
        if cloud not in METHOD_BY_CLOUD:
            raise InvalidRequestError(f"unknown relay cloud {cloud!r}", hint="Use: " + ", ".join(METHOD_BY_CLOUD))
        self.cloud, self.directory, self.name, self._run, self.broker_url = cloud, directory, name, run, broker_ingest_url
        self.retention, self.preflight, self._probe = retention_seconds, preflight or WebhookUrlPreflight(), probe
        self.relay_url: str | None = None

    @property
    def method_id(self) -> str:
        return METHOD_BY_CLOUD[self.cloud]

    def files(self) -> iac.RelayFiles:
        if self.cloud == "aws":
            return iac.aws_sam(self.name, self.retention)
        if self.cloud == "azure":
            return iac.azure_bicep(self.name, self.retention)
        if self.cloud == "gcp":
            return iac.gcp_terraform(self.name, self.retention)
        if self.cloud == "cloudflare":
            return iac.cloudflare()
        return iac.RelayFiles({}, (), "")

    async def plan(self, *, mode: ProvisionMode | None = None) -> ProvisioningPlan:
        supported = frozenset({ProvisionMode.GUIDED} | ({ProvisionMode.API} if self._run is not None and self.cloud != "broker" else set()))
        chosen = pick_mode(mode, supported, (ProvisionMode.API, ProvisionMode.GUIDED))
        warnings = ["the relay runs the same verifier chain as a local receiver: set the webhook secret/token as a platform SECRET, not in the template",
                    f"messages are retained for {self.retention // 3600} h at most (short retention)", "a relay exposes a public URL on a managed service: it is not 'no public URL' (EV section 42)"]
        steps: tuple[ProvisioningStep, ...]
        if self.cloud == "broker":
            if not (self.broker_url or "").startswith("https://"):
                raise InvalidRequestError("the broker ingest URL must be https", hint="Pass broker_ingest_url (the broker's webhook ingest endpoint).", operation="events.provision")
            steps = (ProvisioningStep(f"Register {self.broker_url} as the webhook callback and configure the broker's pull + ack endpoints", chosen, True, True),)
        else:
            f = self.files()
            steps = (ProvisioningStep(f"Write {', '.join(sorted(f.files))} (+ the relay handler) to {self.directory}", chosen, True, False, self.directory),
                     ProvisioningStep(f"Deploy: {' '.join(f.deploy_command)}", chosen, False, True))
        return ProvisioningPlan(self.method_id, steps, {"mode": chosen.value, "cloud": self.cloud, "directory": str(self.directory)}, tuple(warnings))

    def write(self) -> list[Path]:
        written: list[Path] = []
        self.directory.mkdir(parents=True, exist_ok=True)
        for rel, text in self.files().files.items():
            (self.directory / rel).write_text(text, encoding="utf-8")
            written.append(self.directory / rel)
        handler = HANDLERS.get(self.cloud)
        if handler:
            shutil.copy(Path(iac.__file__).parent / handler, self.directory / handler)
            shutil.copy(Path(iac.__file__).parent / "core.py", self.directory / "core.py")
            written += [self.directory / handler, self.directory / "core.py"]
        return written

    async def apply(self, plan: ProvisioningPlan, *, confirm: ConfirmationHandler) -> Mapping[str, str]:
        await confirm_plan(plan, confirm, operation_id="webhook.jira.dynamic.register", target=self.name)
        out = dict(plan.outputs)
        if self.cloud == "broker":
            assert self.broker_url
            self.relay_url = self.broker_url
            out["callback_url"] = self.broker_url
        else:
            files = self.write()
            out["files"] = str(len(files))
            if ProvisionMode(plan.outputs["mode"]) is ProvisionMode.GUIDED or self._run is None:
                out["instructions"] = f"cd {self.directory} && {' '.join(self.files().deploy_command)}"
                return out
            f = self.files()
            text = self._run(list(f.deploy_command), self.directory)
            m = re.search(f.url_hint, text) if f.url_hint else None
            if m:
                self.relay_url = m.group(1).rstrip("/")
                out["callback_url"] = self.relay_url
        if "callback_url" in out:
            report = await self.preflight.check(out["callback_url"])
            if not report.ok:
                out["warnings"] = "; ".join(report.problems)
        return out

    async def verify(self, *, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
        if self._probe is None:
            return VerificationReport(self.method_id, False, None, "no end-to-end probe configured: pass probe=<awaitable returning a VerificationReport>")
        report: VerificationReport = await self._probe(timeout)  # type: ignore[operator]
        return report

    async def teardown(self, *, confirm: ConfirmationHandler) -> None:
        plan = ProvisioningPlan(self.method_id, (ProvisioningStep(f"Remove the generated relay files from {self.directory} (destroy the cloud stack separately)",
                                                                   ProvisionMode.GUIDED, False, True),), {})
        await confirm_plan(plan, confirm, operation_id="webhook.jira.dynamic.delete", target=self.name)
        shutil.rmtree(self.directory, ignore_errors=True)
