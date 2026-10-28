"""Identity and raw-request access (design sections 9.6, 10.1)."""
from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, Literal

from ...core.http import RequestSpec
from ...core.pagination import STRATEGIES
from ...core.urls import ApiSurface
from ...models.identity import AccessibleResource, Identity
from ...models.webhooks import RawResponse
from .base import RestBase

_PRODUCT = {"jira": ApiSurface.JIRA_PLATFORM, "agile": ApiSurface.JIRA_AGILE, "jsm": ApiSurface.JSM,
            "confluence": ApiSurface.CONFLUENCE_V2, "admin": ApiSurface.ORG_ADMIN}


class IdentityRest(RestBase):
    async def whoami(self) -> Identity:
        me = await self._json(ApiSurface.JIRA_PLATFORM, "/myself", operation="identity.whoami")
        return Identity(account_id=me.get("accountId"), display_name=me.get("displayName"), email=me.get("emailAddress"),
                        site=self.transport.urls.site, cloud_id=self.transport.urls.cloud_id,
                        auth_scheme=self.transport.auth.scheme, backend=self.kind.value)

    async def accessible_resources(self) -> list[AccessibleResource]:
        if self.transport.auth.uses_gateway:
            data = await self._json(ApiSurface.JIRA_PLATFORM, "https://api.atlassian.com/oauth/token/accessible-resources",
                                    operation="identity.accessible_resources")
            return [AccessibleResource(id=str(r["id"]), name=r.get("name"), url=r.get("url"), scopes=list(r.get("scopes", [])))
                    for r in data]
        cloud_id = self.transport.urls.cloud_id
        if not cloud_id and self.transport._cloud_resolver is not None:
            cloud_id = await self.transport._cloud_resolver.resolve()
        return [AccessibleResource(id=cloud_id or "", name=self.transport.urls.site, url=f"https://{self.transport.urls.site}")]


class RawRest(RestBase):
    """Anything not covered by a curated or generated operation, with auth, retry and pagination still applied."""

    async def request(self, method: str, path: str, *, product: Literal["jira", "confluence", "jsm", "agile", "admin"],
                      params: Mapping[str, Any] | None = None, json: Any = None,
                      headers: Mapping[str, str] | None = None) -> RawResponse:
        spec = RequestSpec(method.upper(), _PRODUCT[product], path, params or {}, json=json, headers=headers or {},
                           idempotent=method.upper() in ("GET", "HEAD", "PUT", "DELETE"), operation="raw.request")
        resp = await self.transport.send(spec)
        body: Any = None
        if resp.content and "json" in resp.headers.get("content-type", ""):
            body = resp.json()
        return RawResponse(status=resp.status_code, headers=dict(resp.headers), json_body=body,
                           text=None if body is not None else resp.text)

    async def paginate(self, method: str, path: str, *, product: str, strategy: str, params: Mapping[str, Any] | None = None,
                       json: Any = None) -> AsyncIterator[dict[str, Any]]:
        strat = STRATEGIES[strategy]()
        spec = strat.first(RequestSpec(method.upper(), _PRODUCT[product], path, params or {}, json=json, operation="raw.paginate"), 100)
        while True:
            resp = await self.transport.send(spec)
            items, nxt, _ = strat.parse(resp, spec)
            for item in items:
                yield item
            if not nxt:
                return
            spec = strat.next(spec, nxt)
