"""API surfaces and URL resolution (design section 7.1)."""
from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from .errors import ConfigurationError

if TYPE_CHECKING:
    import httpx

    from .auth.base import AuthStrategy


class ApiSurface(StrEnum):
    JIRA_PLATFORM = "jira_platform"      # /rest/api/3
    JIRA_AGILE = "jira_agile"            # /rest/agile/1.0
    JSM = "jsm"                          # /rest/servicedeskapi
    JIRA_WEBHOOKS_ADMIN = "jira_wh"      # /rest/webhooks/1.0
    CONFLUENCE_V2 = "confluence_v2"      # /wiki/api/v2
    CONFLUENCE_V1 = "confluence_v1"      # /wiki/rest/api
    GRAPHQL = "graphql"
    ORG_ADMIN = "org_admin"              # https://api.atlassian.com/admin/v1/orgs/{orgId}
    FORGE_APP_REST = "forge_app_rest"
    DC_JIRA = "dc_jira"                  # {base}/rest/api/2
    DC_CONFLUENCE = "dc_confluence"      # {base}/rest/api


_JIRA_PATHS = {
    ApiSurface.JIRA_PLATFORM: "/rest/api/3",
    ApiSurface.JIRA_AGILE: "/rest/agile/1.0",
    ApiSurface.JSM: "/rest/servicedeskapi",
    ApiSurface.JIRA_WEBHOOKS_ADMIN: "/rest/webhooks/1.0",
}
_CONF_PATHS = {ApiSurface.CONFLUENCE_V2: "/wiki/api/v2", ApiSurface.CONFLUENCE_V1: "/wiki/rest/api"}


class UrlResolver:
    """Builds base URLs. The form depends on the auth strategy (``gateway``).

    | Surface       | Basic / API token                  | OAuth 3LO / service-account bearer                      |
    |---------------|------------------------------------|---------------------------------------------------------|
    | Jira platform | https://{site}/rest/api/3          | https://api.atlassian.com/ex/jira/{cloudId}/rest/api/3  |
    | Confluence v2 | https://{site}/wiki/api/v2         | https://api.atlassian.com/ex/confluence/{cloudId}/wiki/api/v2 |
    | GraphQL       | https://{site}/gateway/api/graphql | https://api.atlassian.com/graphql                       |
    """

    def __init__(self, site: str | None, cloud_id: str | None, gateway: bool, *, org_id: str | None = None,
                 dc_jira_base: str | None = None, dc_confluence_base: str | None = None) -> None:
        self.site = site.removeprefix("https://").rstrip("/") if site else None
        self.cloud_id, self.gateway, self.org_id = cloud_id, gateway, org_id
        self.dc_jira_base = dc_jira_base.rstrip("/") if dc_jira_base else None
        self.dc_confluence_base = dc_confluence_base.rstrip("/") if dc_confluence_base else None

    def base(self, surface: ApiSurface) -> str:
        if surface is ApiSurface.GRAPHQL:
            if self.gateway:
                return "https://api.atlassian.com/graphql"
            return f"https://{self._site()}/gateway/api/graphql"
        if surface is ApiSurface.ORG_ADMIN:
            if not self.org_id:
                raise ConfigurationError("org_id is required for organization admin APIs",
                                         hint="Set ATLASSIAN_ORG_ID and ATLASSIAN_ORG_API_KEY.")
            return f"https://api.atlassian.com/admin/v1/orgs/{self.org_id}"
        if surface is ApiSurface.DC_JIRA:
            if not self.dc_jira_base:
                raise ConfigurationError("Jira Data Center base URL not configured", hint="Set ATLASSIAN_JIRA_DC_BASE_URL.")
            return self.dc_jira_base + "/rest/api/2"
        if surface is ApiSurface.DC_CONFLUENCE:
            if not self.dc_confluence_base:
                raise ConfigurationError("Confluence Data Center base URL not configured",
                                         hint="Set ATLASSIAN_CONFLUENCE_DC_BASE_URL.")
            return self.dc_confluence_base + "/rest/api"
        if surface is ApiSurface.FORGE_APP_REST:
            raise ConfigurationError("Forge App REST URLs are built by ForgeAppRestClient")
        if surface in _JIRA_PATHS:
            return self._root("jira") + _JIRA_PATHS[surface]
        if surface in _CONF_PATHS:
            return self._root("confluence") + _CONF_PATHS[surface]
        raise ConfigurationError(f"unknown surface {surface}")

    def _site(self) -> str:
        if not self.site:
            raise ConfigurationError("site is not configured", hint="Set ATLASSIAN_SITE, e.g. example.atlassian.net.")
        return self.site

    def _root(self, product: str) -> str:
        if self.gateway:
            if not self.cloud_id:
                raise ConfigurationError("cloud_id is required for gateway (OAuth/bearer) URLs",
                                         hint="Resolve it with CloudIdResolver or set ATLASSIAN_CLOUD_ID.")
            return f"https://api.atlassian.com/ex/{product}/{self.cloud_id}"
        return f"https://{self._site()}"


class CloudIdResolver:
    """Resolves the cloudId for a site and caches it (design section 7.1).

    Order: configured value -> OAuth ``accessible-resources`` matched by site -> ``https://{site}/_edge/tenant_info``.
    """

    def __init__(self, site: str | None, auth: AuthStrategy, http: httpx.AsyncClient, *, configured: str | None = None) -> None:
        self.site, self.auth, self.http = site, auth, http
        self._cached = configured

    async def resolve(self) -> str:
        if self._cached:
            return self._cached
        if self.auth.uses_gateway:
            req = self.http.build_request("GET", "https://api.atlassian.com/oauth/token/accessible-resources")
            await self.auth.apply(req)
            resp = await self.http.send(req)
            if resp.status_code != 200:
                raise ConfigurationError("could not list accessible resources", status=resp.status_code,
                                         hint="Check the OAuth scopes and that the app is installed on the site.")
            sites = resp.json()
            match = next((s for s in sites if self.site and self.site in str(s.get("url", ""))), sites[0] if len(sites) == 1 else None)
            if not match:
                raise ConfigurationError("site not found among accessible resources",
                                         hint="Set ATLASSIAN_SITE to one of: " + ", ".join(str(s.get("url")) for s in sites))
            self._cached = str(match["id"])
        else:
            if not self.site:
                raise ConfigurationError("site is not configured", hint="Set ATLASSIAN_SITE.")
            resp = await self.http.get(f"https://{self.site}/_edge/tenant_info")
            if resp.status_code != 200:
                raise ConfigurationError("could not read tenant info", status=resp.status_code,
                                         hint="Check ATLASSIAN_SITE is the exact *.atlassian.net host.")
            self._cached = str(resp.json()["cloudId"])
        return self._cached
