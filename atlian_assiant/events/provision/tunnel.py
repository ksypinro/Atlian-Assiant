"""TunnelProvisioner (DM-44): ngrok or cloudflared in front of the LOCAL receiver. A tunnel creates a PUBLIC URL (EV section 43); never used for strict 'no public URL'."""
from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any, ClassVar, Literal

from ...core.errors import BackendUnavailableError, ConfigurationError
from ...policy import ConfirmationHandler
from ..receivers.preflight import WebhookUrlPreflight
from .base import ProvisioningPlan, ProvisioningStep, ProvisionMode, VerificationReport, confirm_plan, pick_mode

CLASSIFICATION = ("no inbound firewall port: yes", "public URL exists: yes", "satisfies strict 'no public URL': NO")
_CLOUDFLARED_URL = re.compile(r"(https://[a-z0-9-]+\.trycloudflare\.com)")


class TunnelHandle:
    """A running tunnel. ``stop()`` is idempotent."""

    def __init__(self, url: str, stop: Callable[[], None]) -> None:
        self.url, self._stop, self.stopped = url, stop, False

    def stop(self) -> None:
        if not self.stopped:
            self._stop()
            self.stopped = True


def _start_cloudflared(binary: str, port: int) -> TunnelHandle:
    proc = subprocess.Popen([binary, "tunnel", "--url", f"http://localhost:{port}"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)   # noqa: S603 - argv list
    assert proc.stdout is not None
    for line in proc.stdout:
        m = _CLOUDFLARED_URL.search(line)
        if m:
            return TunnelHandle(m.group(1), proc.terminate)
    proc.terminate()
    raise BackendUnavailableError("cloudflared did not report a tunnel URL", hint="Run `cloudflared tunnel --url http://localhost:<port>` by hand to see its output.")


def _start_ngrok(authtoken: str | None, port: int) -> TunnelHandle:
    try:
        import ngrok
    except ImportError as exc:
        raise ConfigurationError("the ngrok SDK is not installed", hint="pip install 'atlian-assiant[tunnels]'") from exc
    listener = ngrok.forward(port, authtoken=authtoken)
    return TunnelHandle(str(listener.url()), listener.close)


class TunnelProvisioner:
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-44"})
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.API})

    def __init__(self, *, provider: Literal["ngrok", "cloudflared"] = "cloudflared", port: int = 8080, path: str = "/hooks/jira", authtoken: str | None = None,
                 binary: str = "cloudflared", starter: Callable[[int], TunnelHandle] | None = None, preflight: WebhookUrlPreflight | None = None,
                 strict_no_public_url: bool = False, probe: Any = None) -> None:
        self.provider, self.port, self.path, self.authtoken, self.binary = provider, port, path, authtoken, binary
        self._starter, self.preflight, self.strict, self._probe = starter, preflight or WebhookUrlPreflight(), strict_no_public_url, probe
        self.handle: TunnelHandle | None = None

    async def plan(self, *, mode: ProvisionMode | None = None) -> ProvisioningPlan:
        pick_mode(mode, self.modes, (ProvisionMode.API,))
        if self.strict:
            from ...core.errors import IngressPolicyError
            raise IngressPolicyError("a tunnel creates a public URL, which a strict 'no public URL' policy forbids",
                                     hint="Use a pull method instead: Forge inbox (DM-07), Automation -> SQS (DM-17), JEC (DM-19) or JQL polling (DM-36).")
        steps = (ProvisioningStep(f"Start {self.provider} in front of http://localhost:{self.port}", ProvisionMode.API, True, False),
                 ProvisioningStep("Run the webhook URL preflight on the public URL", ProvisionMode.API, True, False))
        return ProvisioningPlan("DM-44", steps, {"mode": "api", "provider": self.provider, "port": str(self.port)},
                                (*CLASSIFICATION, "the receiver behind the tunnel MUST run the same verifier chain as any public receiver", "the URL changes when the tunnel restarts: re-register the webhook"))

    async def apply(self, plan: ProvisioningPlan, *, confirm: ConfirmationHandler) -> Mapping[str, str]:
        await confirm_plan(plan, confirm, operation_id="webhook.jira.dynamic.register", target=f"{self.provider}:{self.port}")
        if self._starter is not None:
            self.handle = self._starter(self.port)
        elif self.provider == "ngrok":
            self.handle = _start_ngrok(self.authtoken, self.port)
        else:
            self.handle = _start_cloudflared(self.binary, self.port)
        callback = self.handle.url.rstrip("/") + self.path
        report = await self.preflight.check(callback)
        out = {**plan.outputs, "public_url": self.handle.url, "callback_url": callback, "classification": "; ".join(CLASSIFICATION)}
        if not report.ok:
            out["warnings"] = "; ".join(report.problems)
        return out

    async def verify(self, *, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
        if self._probe is None:
            return VerificationReport("DM-44", False, None, "no end-to-end probe configured: pass probe=verify_dm_44 bound to your inbox")
        report: VerificationReport = await self._probe(timeout)
        return report

    async def teardown(self, *, confirm: ConfirmationHandler) -> None:
        if self.handle is None:
            return
        plan = ProvisioningPlan("DM-44", (ProvisioningStep("Stop the tunnel (the public URL disappears)", ProvisionMode.API, False, False),), {})
        await confirm_plan(plan, confirm, operation_id="webhook.jira.dynamic.delete", target=self.provider)
        self.handle.stop()
