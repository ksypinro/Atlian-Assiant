"""Webhook management and Automation incoming-webhook trigger over Cloud REST (design sections 9.7, 14.3, 14.15)."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from ...core.errors import ConfigurationError, InvalidRequestError, PermissionDeniedError, error_for_status
from ...core.urls import ApiSurface
from ...models.webhooks import FailedWebhook, WebhookRegistration, WebhookSubscription
from .base import RestBase

S = ApiSurface.JIRA_PLATFORM
ADMIN = ApiSurface.JIRA_WEBHOOKS_ADMIN
AUTOMATION_HOSTS = frozenset({"automation.atlassian.com", "api-private.atlassian.com"})
ISSUE_FILTER_KEY = "issue-related-events-section"


def _dt(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def registration(raw: Mapping[str, Any]) -> WebhookRegistration:
    return WebhookRegistration(id=str(raw.get("id")), url=raw.get("url"), events=list(raw.get("events", [])), jql_filter=raw.get("jqlFilter"),
                               expires_at=_dt(raw.get("expirationDate")), enabled=bool(raw.get("enabled", True)))


def admin_registration(raw: Mapping[str, Any]) -> WebhookRegistration:
    ident = raw.get("id") or str(raw.get("self", "")).rstrip("/").rsplit("/", 1)[-1]
    return WebhookRegistration(id=str(ident), url=raw.get("url"), events=list(raw.get("events", [])),
                               jql_filter=(raw.get("filters") or {}).get(ISSUE_FILTER_KEY), enabled=bool(raw.get("enabled", True)))


class WebhookRest(RestBase):
    """Jira dynamic webhooks (``/rest/api/3/webhook``, OAuth 2.0 / Connect only) plus the admin family (``/rest/webhooks/1.0/webhook``)."""

    # ---------------------------------------------------------------- dynamic (DM-03, DM-05)
    async def list_webhooks(self) -> list[WebhookRegistration]:
        out: list[WebhookRegistration] = []
        start = 0
        while True:
            data = await self._json(S, "/webhook", params={"startAt": start, "maxResults": 100}, operation="webhook.jira.dynamic.list")
            values = data.get("values", [])
            out += [registration(v) for v in values]
            if data.get("isLast", True) or not values:
                return out
            start += len(values)

    async def register(self, url: str, subscriptions: Sequence[WebhookSubscription], *, secret: str | None = None) -> list[WebhookRegistration]:
        body: dict[str, Any] = {"url": url, "webhooks": [
            {"events": s.events, **({"jqlFilter": s.jql_filter} if s.jql_filter else {}),
             **({"fieldIdsFilter": s.field_ids_filter} if s.field_ids_filter else {})} for s in subscriptions]}
        try:
            data = await self._json(S, "/webhook", method="POST", json=body, operation="webhook.jira.dynamic.register", idempotent=False)
        except PermissionDeniedError as exc:
            raise PermissionDeniedError(exc.message, hint="Dynamic webhooks can only be registered by OAuth 2.0 (3LO) or Connect apps, not by "
                                        "API-token Basic auth. Use an OAuth client, or register an admin webhook (DM-02) instead.",
                                        status=exc.status, operation=exc.operation, backend=exc.backend) from exc
        results = data.get("webhookRegistrationResult", [])
        errors = [e for r in results for e in r.get("errors", [])]
        if errors:
            raise InvalidRequestError("webhook registration was rejected: " + "; ".join(map(str, errors)),
                                      hint="Check the event names, the JQL filter (only a restricted JQL subset is allowed) and the per-app limits.",
                                      operation="webhook.jira.dynamic.register", details={"errors": errors})
        out = []
        for sub, res in zip(subscriptions, results, strict=False):
            out.append(WebhookRegistration(id=str(res.get("createdWebhookId")), url=url, events=list(sub.events), jql_filter=sub.jql_filter))
        return out

    async def delete(self, webhook_ids: Sequence[str]) -> None:
        await self._json(S, "/webhook", method="DELETE", json={"webhookIds": [int(i) for i in webhook_ids]}, operation="webhook.jira.dynamic.delete")

    async def refresh(self, webhook_ids: Sequence[str]) -> datetime | None:
        data = await self._json(S, "/webhook/refresh", method="PUT", json={"webhookIds": [int(i) for i in webhook_ids]},
                                operation="webhook.jira.dynamic.refresh")
        return _dt((data or {}).get("expirationDate"))

    async def list_failed(self, *, after: datetime | None = None) -> list[FailedWebhook]:
        params: dict[str, Any] = {"maxResults": 100}
        if after is not None:
            params["after"] = int(after.timestamp() * 1000)
        data = await self._json(S, "/webhook/failed", params=params, operation="webhook.jira.dynamic.failed")
        return [FailedWebhook(id=str(v.get("id")), url=v.get("url"), body=v.get("body"), failure_time=_dt(v.get("failureTime")))
                for v in (data or {}).get("values", [])]

    # ---------------------------------------------------------------- admin family (DM-01 / DM-02)
    async def list_admin_webhooks(self) -> list[WebhookRegistration]:
        data = await self._json(ADMIN, "/webhook", operation="webhook.jira.admin.list")
        return [admin_registration(v) for v in (data or [])]

    @staticmethod
    def _admin_body(name: str, url: str, events: Sequence[str], jql: str | None, exclude_body: bool, secret: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name, "url": url, "events": list(events), "excludeBody": exclude_body}
        if jql:
            body["filters"] = {ISSUE_FILTER_KEY: jql}
        if secret:
            body["secret"] = secret                  # exact field is a VERIFY item (VER-01)
        return body

    async def register_admin_webhook(self, name: str, url: str, events: Sequence[str], *, jql: str | None = None, exclude_body: bool = False,
                                     secret: str | None = None) -> WebhookRegistration:
        raw = await self._json(ADMIN, "/webhook", method="POST", json=self._admin_body(name, url, events, jql, exclude_body, secret),
                               operation="webhook.jira.admin.register", idempotent=False)
        return admin_registration(raw)

    async def update_admin_webhook(self, webhook_id: str, name: str, url: str, events: Sequence[str], *, jql: str | None = None,
                                   exclude_body: bool = False, secret: str | None = None) -> WebhookRegistration:
        raw = await self._json(ADMIN, f"/webhook/{webhook_id}", method="PUT", json=self._admin_body(name, url, events, jql, exclude_body, secret),
                               operation="webhook.jira.admin.update")
        return admin_registration(raw)

    async def delete_admin_webhook(self, webhook_id: str) -> None:
        await self._json(ADMIN, f"/webhook/{webhook_id}", method="DELETE", operation="webhook.jira.admin.delete")


class AutomationRest(RestBase):
    """Fires an Automation rule with an *incoming webhook* trigger. The URL is the capability: no Atlassian credentials are sent to it."""

    def _automation_hosts(self) -> frozenset[str]:
        return AUTOMATION_HOSTS

    async def trigger_incoming_webhook(self, webhook_url: str, *, secret: str | None = None, issue_keys: Sequence[str] = (),
                                       data: Mapping[str, Any] | None = None) -> None:
        parts = urlsplit(webhook_url)
        if parts.scheme != "https" or parts.hostname not in self._automation_hosts():
            raise ConfigurationError(f"refusing to call {parts.hostname or webhook_url!r}: not an Automation incoming-webhook host",
                                     hint="Use the URL shown on the rule's 'Incoming webhook' trigger (https://automation.atlassian.com/pg/webhooks/...).",
                                     operation="automation.incoming_webhook.trigger")
        body: dict[str, Any] = {**(dict(data) if data else {})}
        if issue_keys:
            body["issues"] = list(issue_keys)
        headers = {"Content-Type": "application/json", **({"X-Automation-Webhook-Token": secret} if secret else {})}
        request = self.transport.client.build_request("POST", webhook_url, json=body, headers=headers)
        resp = await self.transport.client.send(request)               # deliberately NOT through auth.apply(): never leak API credentials
        if resp.status_code >= 400:
            raise error_for_status(resp.status_code, f"automation incoming webhook returned {resp.status_code}",
                                   operation="automation.incoming_webhook.trigger", backend=self.kind.value)
