"""ForgeAppRestClient: the companion app's App REST API (Preview, EV section 15) at https://api.atlassian.com/svc/{product}/{cloudId}/apps/{appId}_{envId}/..."""
from __future__ import annotations

from typing import Any

from ...core.errors import ConfigurationError
from ...core.http import HttpTransport, RequestSpec
from ...core.urls import ApiSurface


class ForgeAppRestClient:
    """3LO-authenticated calls to the inbox endpoints. The URL shape and the endpoint names are Preview and unverified (VER-06): isolated here."""

    def __init__(self, transport: HttpTransport, *, app_id: str, env_id: str, cloud_id: str, product: str = "jira", base_override: str | None = None) -> None:
        self.transport, self.app_id, self.env_id, self.cloud_id, self.product, self._base = transport, app_id, env_id, cloud_id, product, base_override

    @property
    def base(self) -> str:
        if self._base:
            return self._base.rstrip("/")
        if not (self.app_id and self.env_id and self.cloud_id):
            raise ConfigurationError("forge_app_id, forge_env_id and a cloudId are required", hint="Set ATLASSIAN_FORGE_APP_ID and ATLASSIAN_FORGE_ENV_ID.")
        return f"https://api.atlassian.com/svc/{self.product}/{self.cloud_id}/apps/{self.app_id}_{self.env_id}"

    async def _call(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None, op: str) -> Any:
        return await self.transport.json(RequestSpec(method, ApiSurface.FORGE_APP_REST, self.base + path, params or {}, json=json, operation=op,
                                                     idempotent=method in ("GET", "DELETE")))

    async def events(self, *, cursor: str | None = None, limit: int = 100) -> dict[str, Any]:
        body = await self._call("GET", "/events", params={"cursor": cursor, "limit": limit}, op="forge.inbox.events")
        return dict(body or {})

    async def ack(self, keys: list[str]) -> None:
        await self._call("POST", "/events/ack", json={"ids": keys}, op="forge.inbox.ack")

    async def health(self) -> dict[str, Any]:
        return dict(await self._call("GET", "/health", op="forge.inbox.health") or {})

    async def stats(self) -> dict[str, Any]:
        return dict(await self._call("GET", "/stats", op="forge.inbox.stats") or {})

    async def list_subscriptions(self) -> list[dict[str, Any]]:
        return list(await self._call("GET", "/subscriptions", op="forge.subscriptions.list") or [])

    async def add_subscription(self, module: dict[str, Any]) -> dict[str, Any]:
        return dict(await self._call("POST", "/subscriptions", json=module, op="forge.subscriptions.add") or {})

    async def remove_subscription(self, key: str) -> None:
        await self._call("DELETE", f"/subscriptions/{key}", op="forge.subscriptions.remove")
