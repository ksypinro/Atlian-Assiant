"""HttpTransport: the only component that touches the network for HTTP backends (design section 7.2)."""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

import anyio
import httpx

from .auth.base import AuthStrategy
from .errors import ConfigurationError, error_for_status
from .ratelimit import ConcurrencyLimiter, IssueWriteSerializer, RateLimitObserver
from .retry import RetryPolicy
from .telemetry import Telemetry
from .urls import ApiSurface, CloudIdResolver, UrlResolver

_GATEWAY_RE = re.compile(r"^https://api\.atlassian\.com/ex/(?:jira|confluence)/([^/]+)/")


@dataclass(frozen=True)
class RequestSpec:
    method: str
    surface: ApiSurface
    path: str
    params: Mapping[str, Any] = field(default_factory=dict)
    json: Any = None
    content: bytes | None = None
    files: Mapping[str, Any] | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    idempotent: bool = True
    serialize_key: str | None = None   # e.g. issue key -> per-issue write serialization (DA 5.2)
    operation: str | None = None


def _family(surface: ApiSurface) -> str:
    if surface in (ApiSurface.CONFLUENCE_V1, ApiSurface.CONFLUENCE_V2, ApiSurface.DC_CONFLUENCE):
        return "confluence"
    if surface is ApiSurface.GRAPHQL:
        return "graphql"
    return "jira"


def error_details(resp: httpx.Response) -> tuple[str, dict[str, Any]]:
    """Extract a message plus structured details from Jira (errorMessages/errors) and Confluence (message) bodies."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300] or resp.reason_phrase, {}
    if not isinstance(body, dict):
        return str(body)[:300], {}
    msgs = body.get("errorMessages") or []
    errors = body.get("errors") or {}
    message = "; ".join(map(str, msgs)) or (
        "; ".join(f"{k}: {v}" for k, v in errors.items()) if isinstance(errors, dict) and errors else "") \
        or str(body.get("message") or body.get("error") or resp.reason_phrase)
    return message, {k: body[k] for k in ("errorMessages", "errors", "message", "statusCode") if k in body}


class HttpTransport:
    """Auth injection, URL building, bounded concurrency, retry with Retry-After + jitter, 401 refresh-once,
    error mapping, rate-limit observation, redacted telemetry and cloudId pinning."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        auth: AuthStrategy,
        urls: UrlResolver,
        retry: RetryPolicy | None = None,
        observer: RateLimitObserver | None = None,
        limiter: ConcurrencyLimiter | None = None,
        serializer: IssueWriteSerializer | None = None,
        telemetry: Telemetry | None = None,
        *,
        pinned_cloud_id: str | None = None,
        cloud_id_resolver: CloudIdResolver | None = None,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        backend: str = "rest",
    ) -> None:
        self.client, self.auth, self.urls = client, auth, urls
        self.retry = retry or RetryPolicy()
        self.observer = observer or RateLimitObserver()
        self.limiter = limiter or ConcurrencyLimiter()
        self.serializer = serializer or IssueWriteSerializer()
        self.telemetry = telemetry or Telemetry()
        self.pinned_cloud_id, self._sleep, self.backend = pinned_cloud_id, sleep, backend
        self._cloud_resolver = cloud_id_resolver

    def url_for(self, spec: RequestSpec) -> str:
        if not self.auth.supports(spec.surface):
            raise ConfigurationError(f"auth strategy {self.auth.scheme!r} cannot call {spec.surface.value}",
                                     hint="Configure credentials that support this API surface.", operation=spec.operation)
        if spec.path.startswith("https://"):
            host = spec.path.split("/")[2]
            allowed = {"api.atlassian.com", "auth.atlassian.com", self.urls.site or "",
                       *(u.split("/")[2] for u in (self.urls.dc_jira_base, self.urls.dc_confluence_base) if u)}      # Data Center hosts of THIS configuration only
            if host not in allowed and not host.endswith(".atlassian.net"):
                raise ConfigurationError(f"refusing absolute URL on foreign host {host}",
                                         hint="Absolute URLs are only allowed on the configured Atlassian site or api.atlassian.com.")
            return spec.path
        url = self.urls.base(spec.surface) + spec.path
        if self.pinned_cloud_id:
            m = _GATEWAY_RE.match(url)
            if m and m.group(1) != self.pinned_cloud_id:
                raise ConfigurationError("request targets a different cloudId than the pinned one",
                                         hint="Set pin_cloud_id=false or fix ATLASSIAN_CLOUD_ID.")
        return url

    def _build(self, spec: RequestSpec) -> httpx.Request:
        headers = {"Accept": "application/json", **spec.headers}
        params = {k: v for k, v in spec.params.items() if v is not None}
        return self.client.build_request(spec.method, self.url_for(spec), params=params or None, json=spec.json,
                                         content=spec.content, files=spec.files, headers=headers)

    async def send(self, spec: RequestSpec) -> httpx.Response:
        if self._cloud_resolver is not None and self.urls.gateway and not self.urls.cloud_id:
            self.urls.cloud_id = await self._cloud_resolver.resolve()
        async with self.limiter.limit(_family(spec.surface)), self.serializer.serialize(spec.serialize_key):
            refreshed = False
            attempt = 0
            while True:
                request = self._build(spec)
                await self.auth.apply(request)
                self.telemetry.request({"method": spec.method, "url": str(request.url), "attempt": attempt,
                                        "operation": spec.operation, "headers": dict(request.headers)})
                resp = await self.client.send(request)
                self.observer.observe(spec.surface.value, resp.headers)
                self.telemetry.response({"status": resp.status_code, "url": str(request.url), "attempt": attempt})
                if resp.status_code == 401 and not refreshed and await self.auth.refresh():
                    refreshed = True
                    continue
                if resp.status_code < 400:
                    return resp
                retry_after = self.observer.retry_after(resp.headers)
                if self.retry.should_retry(resp.status_code, idempotent=spec.idempotent, attempt=attempt):
                    await self._sleep(self.retry.delay(attempt, retry_after))
                    attempt += 1
                    continue
                message, details = error_details(resp)
                raise error_for_status(resp.status_code, message, operation=spec.operation, backend=self.backend,
                                       details=details, retry_after=retry_after)

    async def json(self, spec: RequestSpec) -> Any:
        resp = await self.send(spec)
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    async def stream_download(self, spec: RequestSpec, *, max_bytes: int | None = None) -> bytes:
        """Download a binary body, stopping with an error once ``max_bytes`` would be exceeded."""
        resp = await self.send(spec)
        data = resp.content
        if max_bytes is not None and len(data) > max_bytes:
            raise error_for_status(413, f"download exceeds max_bytes={max_bytes}", operation=spec.operation,
                                   backend=self.backend)
        return data


class DcHttpTransport(HttpTransport):
    """Data Center transport. Components are written against the Cloud-named surfaces; here they are mapped onto the Data Center surfaces BEFORE the
    auth check, so a Data Center credential can still never be applied to a Cloud surface (JIRA_PLATFORM -> DC_JIRA ``/rest/api/2``,
    CONFLUENCE_V1 -> DC_CONFLUENCE ``/rest/api``). ``CONFLUENCE_V2`` does not exist on Data Center and is refused."""

    _MAP = {ApiSurface.JIRA_PLATFORM: ApiSurface.DC_JIRA, ApiSurface.CONFLUENCE_V1: ApiSurface.DC_CONFLUENCE}

    def url_for(self, spec: RequestSpec) -> str:
        if spec.surface is ApiSurface.CONFLUENCE_V2:
            raise ConfigurationError("Confluence v2 APIs do not exist on Data Center", hint="Data Center uses the v1 content APIs.", operation=spec.operation)
        if spec.surface in (ApiSurface.JIRA_AGILE, ApiSurface.JSM, ApiSurface.JIRA_WEBHOOKS_ADMIN):
            base = self.urls.dc_jira_base
            if not base:
                raise ConfigurationError("Jira Data Center base URL not configured", hint="Set ATLASSIAN_JIRA_DC_BASE_URL.", operation=spec.operation)
            if not self.auth.supports(spec.surface):
                raise ConfigurationError(f"auth strategy {self.auth.scheme!r} cannot call {spec.surface.value}", operation=spec.operation)
            if spec.path.startswith("https://"):
                return super().url_for(spec)
            from .urls import _JIRA_PATHS
            return base + _JIRA_PATHS[spec.surface] + spec.path
        return super().url_for(replace(spec, surface=self._MAP.get(spec.surface, spec.surface)))
