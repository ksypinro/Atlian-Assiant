"""Header-based strategies: Basic (email + API token), service-account key, PAT, DC Basic, org key, Connect JWT."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import ClassVar
from urllib.parse import parse_qsl, quote, urlsplit

import httpx

from ..urls import ApiSurface
from .base import CLOUD_SURFACES, DC_SURFACES


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class BasicTokenAuth:
    """``Authorization: Basic base64(email:api_token)`` against https://{site}/... (DA 2.1.1). Not gateway-bound."""

    scheme: ClassVar[str] = "basic"
    uses_gateway: ClassVar[bool] = False

    def __init__(self, email: str, api_token: str) -> None:
        self._header = "Basic " + base64.b64encode(f"{email}:{api_token}".encode()).decode()

    def supports(self, surface: ApiSurface) -> bool:
        return surface in CLOUD_SURFACES

    async def apply(self, request: httpx.Request) -> None:
        request.headers["Authorization"] = self._header

    async def refresh(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "BasicTokenAuth(email=***, api_token=***)"


class ServiceAccountKeyAuth:
    """``Authorization: Bearer <api key>`` through the api.atlassian.com gateway (DA 7.3)."""

    scheme: ClassVar[str] = "bearer"
    uses_gateway: ClassVar[bool] = True

    def __init__(self, key: str) -> None:
        self._key = key

    def supports(self, surface: ApiSurface) -> bool:
        return surface in CLOUD_SURFACES

    async def apply(self, request: httpx.Request) -> None:
        request.headers["Authorization"] = f"Bearer {self._key}"

    async def refresh(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "ServiceAccountKeyAuth(key=***)"


class PersonalAccessTokenAuth:
    """Data Center personal access token (Bearer)."""

    scheme: ClassVar[str] = "pat"
    uses_gateway: ClassVar[bool] = False

    def __init__(self, pat: str) -> None:
        self._pat = pat

    def supports(self, surface: ApiSurface) -> bool:
        return surface in DC_SURFACES

    async def apply(self, request: httpx.Request) -> None:
        request.headers["Authorization"] = f"Bearer {self._pat}"

    async def refresh(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "PersonalAccessTokenAuth(pat=***)"


class DcBasicAuth:
    """Data Center Basic auth (discouraged; prefer PAT)."""

    scheme: ClassVar[str] = "dc_basic"
    uses_gateway: ClassVar[bool] = False

    def __init__(self, user: str, password: str) -> None:
        self._header = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    def supports(self, surface: ApiSurface) -> bool:
        return surface in DC_SURFACES

    async def apply(self, request: httpx.Request) -> None:
        request.headers["Authorization"] = self._header

    async def refresh(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "DcBasicAuth(user=***, password=***)"


class OrgApiKeyAuth:
    """Organization API key for api.atlassian.com/admin (audit events and events-stream)."""

    scheme: ClassVar[str] = "org_key"
    uses_gateway: ClassVar[bool] = True

    def __init__(self, key: str) -> None:
        self._key = key

    def supports(self, surface: ApiSurface) -> bool:
        return surface is ApiSurface.ORG_ADMIN

    async def apply(self, request: httpx.Request) -> None:
        request.headers["Authorization"] = f"Bearer {self._key}"

    async def refresh(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "OrgApiKeyAuth(key=***)"


class ConnectJwtAuth:
    """Atlassian Connect ``Authorization: JWT <HS256>`` with the query-string hash (qsh) claim (legacy, DM-04)."""

    scheme: ClassVar[str] = "connect_jwt"
    uses_gateway: ClassVar[bool] = False

    def __init__(self, client_key: str, shared_secret: str, *, lifetime: int = 180) -> None:
        self._iss, self._secret, self._lifetime = client_key, shared_secret.encode(), lifetime

    def supports(self, surface: ApiSurface) -> bool:
        return surface in CLOUD_SURFACES

    @staticmethod
    def query_string_hash(method: str, url: str) -> str:
        parts = urlsplit(url)
        params = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "jwt")
        canonical_query = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in params)
        canonical = f"{method.upper()}&{parts.path.rstrip('/') or '/'}&{canonical_query}"
        return hashlib.sha256(canonical.encode()).hexdigest()

    def token(self, method: str, url: str, now: int | None = None) -> str:
        issued = int(time.time()) if now is None else now
        header = _b64(json.dumps({"typ": "JWT", "alg": "HS256"}, separators=(",", ":")).encode())
        base_path = urlsplit(url)
        # The qsh is computed over the path relative to the context path; here the full path is used.
        claims = {"iss": self._iss, "iat": issued, "exp": issued + self._lifetime,
                  "qsh": self.query_string_hash(method, f"{base_path.scheme}://{base_path.netloc}{base_path.path}?{base_path.query}")}
        payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
        signature = _b64(hmac.new(self._secret, f"{header}.{payload}".encode(), hashlib.sha256).digest())
        return f"{header}.{payload}.{signature}"

    async def apply(self, request: httpx.Request) -> None:
        request.headers["Authorization"] = "JWT " + self.token(request.method, str(request.url))

    async def refresh(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "ConnectJwtAuth(client_key=***, shared_secret=***)"
