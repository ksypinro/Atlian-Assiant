"""Data Center webhook provisioners (DM-46 Confluence DC, DM-47 Jira DC): receivers live inside the same private network (EV section 45)."""
from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any, ClassVar

import httpx

from ...core.errors import AtlassianError, InvalidRequestError, error_for_status
from ...policy import ConfirmationHandler
from .base import ProvisioningPlan, ProvisioningStep, ProvisionMode, VerificationReport, confirm_plan, pick_mode


class DcWebhookClient:
    """Minimal DC webhook API client (Bearer PAT). Confluence DC: ``/rest/api/webhooks`` (+ ``test``, ``latest``, ``statistics``); Jira DC: ``/rest/webhooks/1.0/webhook``."""

    def __init__(self, http: httpx.AsyncClient, base_url: str, token: str, *, product: str) -> None:
        self.http, self.base, self.product = http, base_url.rstrip("/"), product
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"}
        self._root = "/rest/api/webhooks" if product == "confluence" else "/rest/webhooks/1.0/webhook"

    async def _call(self, method: str, path: str = "", *, json: Any = None, params: Mapping[str, Any] | None = None) -> Any:
        resp = await self.http.request(method, self.base + self._root + path, headers=self._headers, json=json, params=params)
        if resp.status_code >= 400:
            raise error_for_status(resp.status_code, f"{method} {self._root}{path} returned {resp.status_code}", operation=f"webhook.{self.product}_dc", backend="rest_dc")
        return resp.json() if resp.content else None

    async def list(self) -> list[dict[str, Any]]:
        body = await self._call("GET")
        return list(body.get("results", body) if isinstance(body, dict) else body or [])

    async def create(self, hook: Mapping[str, Any]) -> dict[str, Any]:
        return dict(await self._call("POST", json=dict(hook)))

    async def update(self, hook_id: str, hook: Mapping[str, Any]) -> dict[str, Any]:
        return dict(await self._call("PUT", f"/{hook_id}", json=dict(hook)))

    async def delete(self, hook_id: str) -> None:
        await self._call("DELETE", f"/{hook_id}")

    async def test(self, url: str) -> None:
        await self._call("POST", "/test", json={"url": url})

    async def latest(self, hook_id: str) -> dict[str, Any] | None:
        out = await self._call("GET", f"/{hook_id}/latest")
        return dict(out) if out else None

    async def statistics(self, hook_id: str) -> dict[str, Any]:
        return dict(await self._call("GET", f"/{hook_id}/statistics") or {})


class _DcProvisioner:
    method_ids: ClassVar[frozenset[str]]
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.API, ProvisionMode.GUIDED})
    method_id: str
    product: str

    def __init__(self, api: DcWebhookClient | None, *, callback_url: str, events: Sequence[str], name: str = "atlian-assiant", secret: str | None = None,
                 jql: str | None = None, probe: Any = None) -> None:
        self.api, self.callback_url, self.events, self.name, self.secret, self.jql, self._probe = api, callback_url, list(events), name, secret, jql, probe
        self.webhook_id: str | None = None

    def _hook(self) -> dict[str, Any]:
        hook: dict[str, Any] = {"name": self.name, "url": self.callback_url, "events": self.events, "active": True}
        if self.secret:
            hook["secret"] = self.secret                 # the signature header/algorithm for DC webhooks is unverified: configurable on the receiver
        if self.jql and self.product == "jira":
            hook["filters"] = {"issue-related-events-section": self.jql}
        return hook

    def _check_url(self) -> list[str]:
        w: list[str] = []
        if not self.callback_url.startswith(("http://", "https://")):
            w.append("callback URL must be http(s)")
        if self.callback_url.startswith("http://"):
            w.append("callback is plain HTTP: acceptable only inside the private network; add the verifier chain (secret + IP allow-list)")
        return w

    async def plan(self, *, mode: ProvisionMode | None = None) -> ProvisioningPlan:
        chosen = pick_mode(mode, frozenset({ProvisionMode.GUIDED} | ({ProvisionMode.API} if self.api is not None else set())), (ProvisionMode.API, ProvisionMode.GUIDED))
        bad = [w for w in self._check_url() if "must be" in w]
        if bad:
            raise InvalidRequestError(bad[0], hint="Use the receiver's LAN address, e.g. https://hooks.corp.example/hooks/jira-dc.", operation="events.provision")
        steps = (ProvisioningStep(f"Create or update the {self.product.title()} Data Center webhook {self.name!r} -> {self.callback_url}", chosen, True, True),)
        return ProvisioningPlan(self.method_id, steps, {"mode": chosen.value, "callback_url": self.callback_url}, tuple(self._check_url()))

    async def apply(self, plan: ProvisioningPlan, *, confirm: ConfirmationHandler) -> Mapping[str, str]:
        await confirm_plan(plan, confirm, operation_id="webhook.confluence_dc.register" if self.product == "confluence" else "webhook.jira.admin.register", target=self.callback_url)
        if ProvisionMode(plan.outputs["mode"]) is ProvisionMode.GUIDED or self.api is None:
            where = "Confluence admin > Webhooks" if self.product == "confluence" else "Jira administration > System > WebHooks"
            return {**plan.outputs, "instructions": f"{where}: name={self.name}; URL={self.callback_url}; events={', '.join(self.events)}"}
        existing = next((h for h in await self.api.list() if h.get("url") == self.callback_url), None)
        hook = await (self.api.update(str(existing["id"]), self._hook()) if existing else self.api.create(self._hook()))
        self.webhook_id = str(hook.get("id") or (existing or {}).get("id"))
        return {**plan.outputs, "webhook_id": self.webhook_id}

    async def verify(self, *, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
        if self._probe is None:
            return VerificationReport(self.method_id, False, None, "no end-to-end probe configured: pass probe=<awaitable returning a VerificationReport>")
        report: VerificationReport = await self._probe(timeout)
        return report

    async def teardown(self, *, confirm: ConfirmationHandler) -> None:
        if self.api is None or self.webhook_id is None:
            return
        plan = ProvisioningPlan(self.method_id, (ProvisioningStep(f"Delete webhook {self.webhook_id}", ProvisionMode.API, False, True),), {"webhook_id": self.webhook_id})
        await confirm_plan(plan, confirm, operation_id="webhook.confluence_dc.delete" if self.product == "confluence" else "webhook.jira.admin.delete", target=self.webhook_id)
        await self.api.delete(self.webhook_id)
        self.webhook_id = None


class ConfluenceDCWebhookProvisioner(_DcProvisioner):
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-46"})
    method_id = "DM-46"
    product = "confluence"

    async def send_test(self) -> VerificationReport:
        """Confluence DC can send a test delivery (``/webhooks/test``): proves LAN reachability without changing any content."""
        started = time.monotonic()
        if self.api is None:
            return VerificationReport("DM-46", False, None, "no API client: guided mode cannot send a test delivery")
        try:
            await self.api.test(self.callback_url)
        except AtlassianError as exc:
            return VerificationReport("DM-46", False, None, f"test delivery failed: {type(exc).__name__}: {exc.message}")
        return VerificationReport("DM-46", True, time.monotonic() - started, "test delivery requested")


class JiraDCWebhookProvisioner(_DcProvisioner):
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-47"})
    method_id = "DM-47"
    product = "jira"
