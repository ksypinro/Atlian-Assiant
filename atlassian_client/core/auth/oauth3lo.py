"""OAuth 2.0 (3LO) with refresh-token rotation persisted to disk with 0600 permissions (DA 2.1.2)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import ClassVar

import anyio
import httpx

from ..errors import AuthenticationError
from ..urls import ApiSurface
from .base import CLOUD_SURFACES

TOKEN_URL = "https://auth.atlassian.com/oauth/token"


class OAuth3LOAuth:
    scheme: ClassVar[str] = "oauth_3lo"
    uses_gateway: ClassVar[bool] = True

    def __init__(self, client_id: str, client_secret: str, refresh_token: str, *, store: Path | None = None,
                 http: httpx.AsyncClient | None = None, token_url: str = TOKEN_URL) -> None:
        self._client_id, self._client_secret = client_id, client_secret
        self._refresh_token = refresh_token
        self._store, self._http, self._token_url = store, http, token_url
        self._access: str | None = None
        self._expires_at = 0.0
        self._lock = anyio.Lock()
        if store is not None and store.exists():
            try:
                self._refresh_token = json.loads(store.read_text(encoding="utf-8"))["refresh_token"]
            except (KeyError, ValueError):
                pass  # fall back to the configured refresh token

    def supports(self, surface: ApiSurface) -> bool:
        return surface in CLOUD_SURFACES

    async def apply(self, request: httpx.Request) -> None:
        if self._access is None or time.time() >= self._expires_at - 30:
            await self.refresh()
        request.headers["Authorization"] = f"Bearer {self._access}"

    async def refresh(self) -> bool:
        async with self._lock:
            payload = {"grant_type": "refresh_token", "client_id": self._client_id,
                       "client_secret": self._client_secret, "refresh_token": self._refresh_token}
            client = self._http or httpx.AsyncClient()
            try:
                resp = await client.post(self._token_url, json=payload)
            finally:
                if self._http is None:
                    await client.aclose()
            if resp.status_code != 200:
                raise AuthenticationError("OAuth token refresh failed", status=resp.status_code,
                                          hint="The refresh token may be revoked or expired; re-run the OAuth consent flow.")
            body = resp.json()
            self._access = str(body["access_token"])
            self._expires_at = time.time() + float(body.get("expires_in", 3600))
            rotated = body.get("refresh_token")
            if rotated and rotated != self._refresh_token:
                self._refresh_token = str(rotated)
                self._persist()
            return True

    def _persist(self) -> None:
        if self._store is None:
            return
        self._store.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._store, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"refresh_token": self._refresh_token}, fh)
        os.chmod(self._store, 0o600)

    def __repr__(self) -> str:
        return "OAuth3LOAuth(client_id=***, refresh_token=***)"
