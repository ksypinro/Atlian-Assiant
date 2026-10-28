"""Authentication strategy protocol (design section 7.3)."""
from __future__ import annotations

from typing import ClassVar, Protocol, runtime_checkable

import httpx

from ..urls import ApiSurface


@runtime_checkable
class AuthStrategy(Protocol):
    scheme: ClassVar[str]
    uses_gateway: ClassVar[bool]         # True -> https://api.atlassian.com/ex/{product}/{cloudId}

    def supports(self, surface: ApiSurface) -> bool: ...

    async def apply(self, request: httpx.Request) -> None: ...

    async def refresh(self) -> bool:
        """Refresh credentials after a 401. Return True if the request should be retried once."""
        ...


CLOUD_SURFACES = frozenset({
    ApiSurface.JIRA_PLATFORM, ApiSurface.JIRA_AGILE, ApiSurface.JSM, ApiSurface.JIRA_WEBHOOKS_ADMIN,
    ApiSurface.CONFLUENCE_V2, ApiSurface.CONFLUENCE_V1, ApiSurface.GRAPHQL, ApiSurface.FORGE_APP_REST,
})
DC_SURFACES = frozenset({ApiSurface.DC_JIRA, ApiSurface.DC_CONFLUENCE, ApiSurface.JIRA_WEBHOOKS_ADMIN, ApiSurface.JIRA_AGILE,
                         ApiSurface.JSM})
