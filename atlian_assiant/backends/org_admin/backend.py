"""ORG_ADMIN backend: organization audit APIs on api.atlassian.com/admin/v1/orgs/{orgId} (design section 10.11, EV sections 18-19)."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, ClassVar, Literal
from urllib.parse import parse_qs, urlsplit

from ...capabilities import BackendKind, Deployment
from ...config import AtlassianConfig
from ...core.auth.static import OrgApiKeyAuth
from ...core.errors import InvalidRequestError
from ...core.http import HttpTransport
from ...core.pagination import Cursor
from ...core.services import CoreServices
from ...core.urls import ApiSurface, UrlResolver
from ...factory import Availability
from ...models.admin import AuditEvent, AuditEventPage
from ...models.admin import audit_event_from_api as audit_event
from ...models.common import Page
from ...protocols.base import BackendHealth
from ..rest_cloud.base import RestBase

S = ApiSurface.ORG_ADMIN
DEFAULT_PAGE, MAX_PAGE = 200, 500
SUPPORTED: frozenset[str] = frozenset({"admin.audit.list", "admin.audit.get", "admin.audit.actions", "admin.audit.stream"})


def next_cursor_of(body: Mapping[str, Any]) -> str | None:
    """The continuation token: ``meta.next``, else the ``cursor`` query parameter of ``links.next``."""
    meta = body.get("meta") or {}
    if meta.get("next"):
        return str(meta["next"])
    link = (body.get("links") or {}).get("next")
    if link:
        found = parse_qs(urlsplit(str(link)).query).get("cursor")
        return found[0] if found else None
    return None


class OrgAuditRest(RestBase):
    kind = BackendKind.ORG_ADMIN

    @staticmethod
    def _limit(limit: int) -> int:
        if limit < 1:
            raise InvalidRequestError("limit must be at least 1", hint=f"Use 1..{MAX_PAGE}.")
        return min(limit, MAX_PAGE)

    def _wrap(self, strategy: str, token: str | None) -> str | None:
        return Cursor(self.kind, strategy, {"cursor": token}).encode() if token else None

    @staticmethod
    def _unwrap(cursor: str | None, strategy: str) -> str | None:
        if not cursor:
            return None
        c = Cursor.decode(cursor)
        if c.strategy != strategy:
            raise InvalidRequestError("cursor does not belong to this operation", hint="Pass `next_cursor` only to the operation that returned it.")
        return str(c.state.get("cursor")) if c.state.get("cursor") else None

    async def list_events(self, *, start: datetime | None = None, end: datetime | None = None, action: str | None = None, query: str | None = None,
                          cursor: str | None = None, limit: int = DEFAULT_PAGE) -> Page[AuditEvent]:
        params: dict[str, Any] = {"limit": self._limit(limit), "cursor": self._unwrap(cursor, "org_events"), "action": action, "q": query,
                                  "from": int(start.timestamp() * 1000) if start else None, "to": int(end.timestamp() * 1000) if end else None}
        body = await self._json(S, "/events", params=params, operation="admin.audit.list")
        return Page(items=[audit_event(e) for e in body.get("data", [])], next_cursor=self._wrap("org_events", next_cursor_of(body)),
                    total=(body.get("meta") or {}).get("total"))

    async def get_event(self, event_id: str) -> AuditEvent:
        body = await self._json(S, f"/events/{event_id}", operation="admin.audit.get")
        return audit_event(body.get("data", body))

    async def list_event_actions(self) -> list[str]:
        body = await self._json(S, "/event-actions", operation="admin.audit.actions")
        data = body.get("data", body)
        items = data.get("actions", data) if isinstance(data, Mapping) else data
        return [str(a.get("id") if isinstance(a, Mapping) else a) for a in items]

    async def poll_stream(self, *, cursor: str | None = None, start: datetime | None = None, limit: int = 500,
                          sort_order: Literal["asc", "desc"] = "asc") -> AuditEventPage:
        """Cursor-based poll ordered by ``processedAt``. The cursor is returned even on the last page: checkpoint it."""
        params: dict[str, Any] = {"limit": self._limit(limit), "cursor": self._unwrap(cursor, "org_stream"), "sortOrder": sort_order,
                                  "from": int(start.timestamp() * 1000) if start and not cursor else None}
        body = await self._json(S, "/events-stream", params=params, operation="admin.audit.stream")
        token = next_cursor_of(body) or self._unwrap(cursor, "org_stream")                 # keep the old cursor when nothing new arrived
        return AuditEventPage(events=[audit_event(e) for e in body.get("data", [])], next_cursor=self._wrap("org_stream", token))


class OrgAdminBackend:
    kind: ClassVar[BackendKind] = BackendKind.ORG_ADMIN
    deployments: ClassVar[frozenset[Deployment]] = frozenset({Deployment.CLOUD})

    def __init__(self, transport: HttpTransport, core: CoreServices | None = None) -> None:
        self.transport, self._core = transport, core
        self.audit = OrgAuditRest(transport)

    def implementation_for(self, protocol: type) -> object | None:
        return self.audit if isinstance(self.audit, protocol) else None

    def supported_operations(self) -> frozenset[str]:
        return SUPPORTED

    async def health(self) -> BackendHealth:
        try:
            await self.audit.list_event_actions()
        except Exception as exc:
            return BackendHealth("unavailable", f"{type(exc).__name__}: {exc}")
        return BackendHealth("ready", "audit completeness depends on exclusions and plan entitlements (EV section 21)")

    async def aclose(self) -> None:
        if self._core is not None:
            await self._core.aclose()


class OrgAdminBackendFactory:
    kind: ClassVar[BackendKind] = BackendKind.ORG_ADMIN
    deployments: ClassVar[frozenset[Deployment]] = frozenset({Deployment.CLOUD})
    extra: ClassVar[str | None] = None

    def availability(self, config: AtlassianConfig) -> Availability:
        if not config.org_id:
            return Availability(False, "ATLASSIAN_ORG_ID is not set")
        if not config.org_api_key:
            return Availability(False, "ATLASSIAN_ORG_API_KEY is not set (create an organization API key in admin.atlassian.com)")
        return Availability(True)

    def create(self, config: AtlassianConfig, core: CoreServices) -> OrgAdminBackend:
        assert config.org_api_key is not None
        auth = OrgApiKeyAuth(config.org_api_key.get_secret_value())
        urls = UrlResolver(config.site, config.cloud_id, True, org_id=config.org_id)
        transport = HttpTransport(core.http, auth, urls, core.retry, core.observer, core.limiter, core.serializer, core.telemetry, backend="org_admin")
        return OrgAdminBackend(transport, core)
