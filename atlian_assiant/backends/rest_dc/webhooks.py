"""Data Center webhooks and Automation trigger (design 9.7, 14.3, DM-01/02/45/46).

Jira DC: the admin family ``/rest/webhooks/1.0/webhook`` (inherited). Confluence DC: ``/rest/api/webhooks`` (+ ``test`` / ``statistics``).
Cloud *dynamic* webhooks do not exist on Data Center and are refused with the alternative named.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from ...core.errors import DeploymentMismatchError
from ...core.urls import ApiSurface
from ...models.webhooks import FailedWebhook, WebhookRegistration, WebhookSubscription
from ..rest_cloud.webhooks import AutomationRest, WebhookRest

C = ApiSurface.CONFLUENCE_V1                                  # mapped to the Data Center Confluence base by DcHttpTransport


def _dynamic(op: str) -> DeploymentMismatchError:
    return DeploymentMismatchError(f"{op}: dynamic webhooks exist on Jira Cloud only", operation=op,
                                   hint="Use the admin webhook family (register_admin_webhook) on Data Center.")


def confluence_registration(raw: dict[str, Any]) -> WebhookRegistration:
    return WebhookRegistration(id=str(raw.get("id")), url=raw.get("url"), events=list(raw.get("events", [])), enabled=bool(raw.get("active", raw.get("enabled", True))))


class WebhookDcRest(WebhookRest):
    async def list_webhooks(self) -> list[WebhookRegistration]:
        return await self.list_admin_webhooks()

    async def register(self, url: str, subscriptions: Sequence[WebhookSubscription], *, secret: str | None = None) -> list[WebhookRegistration]:
        out = []
        for i, sub in enumerate(subscriptions, 1):
            out.append(await self.register_admin_webhook(f"atlian-assiant-{i}", url, sub.events, jql=sub.jql_filter, secret=secret))
        return out

    async def delete(self, webhook_ids: Sequence[str]) -> None:
        for wid in webhook_ids:
            await self.delete_admin_webhook(wid)

    async def refresh(self, webhook_ids: Sequence[str]) -> datetime | None:
        raise _dynamic("webhook.jira.dynamic.refresh")           # admin webhooks do not expire

    async def list_failed(self, *, after: datetime | None = None) -> list[FailedWebhook]:
        raise _dynamic("webhook.jira.dynamic.failed")

    # ---------------------------------------------------------------- Confluence DC (DM-46)
    async def list_confluence_webhooks(self) -> list[WebhookRegistration]:
        data = await self._json(C, "/webhooks", operation="webhook.confluence_dc.list")
        return [confluence_registration(v) for v in (data.get("results", data) if isinstance(data, dict) else data or [])]

    async def register_confluence_webhook(self, name: str, url: str, events: Sequence[str], *, secret: str | None = None, active: bool = True) -> WebhookRegistration:
        body: dict[str, Any] = {"name": name, "url": url, "events": list(events), "active": active, "scope": "global"}
        if secret:
            body["credentials"] = {"secret": secret}
        return confluence_registration(await self._json(C, "/webhooks", method="POST", json=body, operation="webhook.confluence_dc.register", idempotent=False))

    async def update_confluence_webhook(self, webhook_id: str, name: str, url: str, events: Sequence[str], *, active: bool = True) -> WebhookRegistration:
        body = {"name": name, "url": url, "events": list(events), "active": active, "scope": "global"}
        return confluence_registration(await self._json(C, f"/webhooks/{webhook_id}", method="PUT", json=body, operation="webhook.confluence_dc.update"))

    async def delete_confluence_webhook(self, webhook_id: str) -> None:
        await self._json(C, f"/webhooks/{webhook_id}", method="DELETE", operation="webhook.confluence_dc.delete")

    async def test_confluence_webhook(self, url: str) -> Any:
        return await self._json(C, "/webhooks/test", method="POST", json={"url": url}, operation="webhook.confluence_dc.test", idempotent=False)

    async def confluence_webhook_statistics(self, webhook_id: str) -> dict[str, Any]:
        return dict(await self._json(C, f"/webhooks/{webhook_id}/statistics", operation="webhook.confluence_dc.stats") or {})


class AutomationDcRest(AutomationRest):
    """Automation for Jira (Data Center app) incoming webhooks live on the Jira host itself (``/rest/cb-automation/latest/hooks/...``)."""

    def _automation_hosts(self) -> frozenset[str]:
        base = self.transport.urls.dc_jira_base
        return frozenset({urlsplit(base).hostname or ""}) if base else frozenset()
