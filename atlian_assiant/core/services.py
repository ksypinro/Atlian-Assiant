"""Shared infrastructure handed to every backend factory (design section 12.1)."""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from ..config import AtlassianConfig
from .auth import BasicTokenAuth, OAuth3LOAuth, PersonalAccessTokenAuth, ServiceAccountKeyAuth
from .auth.base import AuthStrategy
from .cache import MetadataCache
from .errors import ConfigurationError
from .http import DcHttpTransport, HttpTransport
from .ratelimit import ConcurrencyLimiter, IssueWriteSerializer, RateLimitObserver
from .redaction import Redactor
from .retry import RetryPolicy
from .telemetry import Telemetry
from .urls import CloudIdResolver, UrlResolver


def user_agent(config: AtlassianConfig) -> str:
    """``atlian-assiant/<version>`` plus the optional suffix. Header values must not end in whitespace (httpx refuses to send them)."""
    from .._version import __version__

    suffix = (config.user_agent_suffix or "").strip()
    return f"atlian-assiant/{__version__}" + (f" {suffix}" if suffix else "")


def collect_secrets(config: AtlassianConfig) -> list[str]:
    values = [config.api_token, config.service_account_key, config.oauth_client_secret, config.oauth_refresh_token,
              config.pat, config.dc_basic_password, config.org_api_key, config.db_url]
    return [v.get_secret_value() for v in values if v is not None]


def build_cloud_auth(config: AtlassianConfig, http: httpx.AsyncClient) -> AuthStrategy | None:
    """Pick the Cloud auth strategy: OAuth 3LO, then service-account key, then email + API token."""
    if config.oauth_client_id and config.oauth_client_secret and config.oauth_refresh_token:
        return OAuth3LOAuth(config.oauth_client_id, config.oauth_client_secret.get_secret_value(),
                            config.oauth_refresh_token.get_secret_value(), store=config.oauth_token_store, http=http)
    if config.service_account_key:
        return ServiceAccountKeyAuth(config.service_account_key.get_secret_value())
    if config.email and config.api_token:
        return BasicTokenAuth(config.email, config.api_token.get_secret_value())
    return None


def build_dc_auth(config: AtlassianConfig) -> AuthStrategy | None:
    from .auth import DcBasicAuth

    if config.pat:
        return PersonalAccessTokenAuth(config.pat.get_secret_value())
    if config.dc_basic_user and config.dc_basic_password:
        return DcBasicAuth(config.dc_basic_user, config.dc_basic_password.get_secret_value())
    return None


@dataclass
class CoreServices:
    """One per client. ``http`` may be injected (tests use ``httpx.MockTransport`` / respx)."""

    config: AtlassianConfig
    http: httpx.AsyncClient
    telemetry: Telemetry
    cache: MetadataCache = field(default_factory=MetadataCache)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    observer: RateLimitObserver = field(default_factory=RateLimitObserver)
    limiter: ConcurrencyLimiter = field(default_factory=ConcurrencyLimiter)
    serializer: IssueWriteSerializer = field(default_factory=IssueWriteSerializer)
    sleep: object | None = None

    @classmethod
    def create(cls, config: AtlassianConfig, http: httpx.AsyncClient | None = None) -> CoreServices:
        client = http or httpx.AsyncClient(timeout=config.timeout_seconds, follow_redirects=True,
                                           headers={"User-Agent": user_agent(config)})
        return cls(config=config, http=client, telemetry=Telemetry(Redactor(collect_secrets(config))),
                   retry=RetryPolicy(max_attempts=config.max_retries),
                   limiter=ConcurrencyLimiter(default=config.max_concurrency))

    def cloud_transport(self, backend: str = "rest") -> HttpTransport:
        auth = build_cloud_auth(self.config, self.http)
        if auth is None:
            raise ConfigurationError("no Cloud credentials configured",
                                     hint="Set ATLASSIAN_EMAIL + ATLASSIAN_API_TOKEN (and ATLASSIAN_SITE), or OAuth / "
                                          "service-account settings.")
        urls = UrlResolver(self.config.site, self.config.cloud_id, auth.uses_gateway, org_id=self.config.org_id)
        resolver = CloudIdResolver(self.config.site, auth, self.http, configured=self.config.cloud_id)
        return HttpTransport(self.http, auth, urls, self.retry, self.observer, self.limiter, self.serializer, self.telemetry,
                             pinned_cloud_id=self.config.cloud_id if self.config.pin_cloud_id else None,
                             cloud_id_resolver=resolver, backend=backend)

    def dc_transport(self, backend: str = "rest_dc") -> HttpTransport:
        """Transport for Data Center: PAT (or discouraged Basic) auth, DC base URLs, no cloudId handling."""
        auth = build_dc_auth(self.config)
        if auth is None:
            raise ConfigurationError("no Data Center credentials configured", hint="Set ATLASSIAN_PAT (personal access token), or ATLASSIAN_DC_BASIC_USER + ATLASSIAN_DC_BASIC_PASSWORD.")
        urls = UrlResolver(None, None, False, dc_jira_base=str(self.config.jira_dc_base_url) if self.config.jira_dc_base_url else None,
                           dc_confluence_base=str(self.config.confluence_dc_base_url) if self.config.confluence_dc_base_url else None)
        return DcHttpTransport(self.http, auth, urls, self.retry, self.observer, self.limiter, self.serializer, self.telemetry, backend=backend)

    async def aclose(self) -> None:
        await self.http.aclose()
