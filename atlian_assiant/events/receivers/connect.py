"""Connect (legacy) receivers: lifecycle callbacks and webhooks, with per-tenant shared secrets (DM-04, EV sections 4.4, 46). Prefer Forge for new work."""
from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar, Protocol

from ...core.errors import ConfigurationError
from ..model import ChangeEvent, EventSourceKind
from ..normalize import normalize_jira_webhook
from ..stores.base import DedupStore, LocalQueue
from .base import ConnectJwtVerifier, InboundRequest, InboundResponse, QueueEndpoint, RequestVerifier


class ConnectSecretStore(Protocol):
    async def shared_secret(self, client_key: str) -> str | None: ...
    async def put(self, client_key: str, shared_secret: str, base_url: str | None = None) -> None: ...
    async def delete(self, client_key: str) -> None: ...
    async def tenants(self) -> list[str]: ...


class MemoryConnectStore:
    def __init__(self) -> None:
        self._v: dict[str, tuple[str, str | None]] = {}

    async def shared_secret(self, client_key: str) -> str | None:
        return self._v.get(client_key, (None, None))[0]

    async def put(self, client_key: str, shared_secret: str, base_url: str | None = None) -> None:
        self._v[client_key] = (shared_secret, base_url)

    async def delete(self, client_key: str) -> None:
        self._v.pop(client_key, None)

    async def tenants(self) -> list[str]:
        return sorted(self._v)


class EncryptedFileConnectStore:
    """Shared secrets encrypted at rest with Fernet (AES-128-CBC + HMAC) in a 0600 file. The key comes from ``key`` (urlsafe base64, 32 bytes): keep it in a
    secret store, not next to the file. Needs the ``jwt`` extra (``cryptography``)."""

    def __init__(self, path: str | Path, key: str | bytes) -> None:
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:
            raise ConfigurationError("cryptography is not installed", hint="pip install 'atlian-assiant[jwt]'") from exc
        self.path, self._fernet = Path(path), Fernet(key)

    @staticmethod
    def generate_key() -> str:
        from cryptography.fernet import Fernet
        return Fernet.generate_key().decode()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return dict(json.loads(self._fernet.decrypt(self.path.read_bytes())))

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(self._fernet.encrypt(json.dumps(data).encode()))
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        tmp.replace(self.path)

    async def shared_secret(self, client_key: str) -> str | None:
        entry = self._load().get(client_key)
        return None if entry is None else str(entry["secret"])

    async def put(self, client_key: str, shared_secret: str, base_url: str | None = None) -> None:
        data = self._load()
        data[client_key] = {"secret": shared_secret, "base_url": base_url}
        self._save(data)

    async def delete(self, client_key: str) -> None:
        data = self._load()
        data.pop(client_key, None)
        self._save(data)

    async def tenants(self) -> list[str]:
        return sorted(self._load())


class ConnectLifecycleEndpoint:
    """``installed`` stores the tenant's ``clientKey``/``sharedSecret``; ``uninstalled`` WIPES them. Re-installs and uninstalls must carry a valid JWT signed
    with the CURRENT secret, so an attacker cannot replace or delete a tenant's secret by posting a forged lifecycle call."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-04"})

    def __init__(self, store: ConnectSecretStore, *, installed_path: str = "/connect/installed", uninstalled_path: str = "/connect/uninstalled") -> None:
        self.store, self.installed_path, self.uninstalled_path = store, installed_path, uninstalled_path
        self.path = installed_path
        self.verifiers: Sequence[RequestVerifier] = ()

    async def handle(self, request: InboundRequest) -> InboundResponse:
        if request.method.upper() != "POST":
            return InboundResponse(405)
        try:
            body = json.loads(request.body or b"{}")
            client_key = str(body["clientKey"])
        except (ValueError, KeyError, TypeError):
            return InboundResponse(400)
        existing = await self.store.shared_secret(client_key)
        if request.path.split("?")[0] == self.installed_path:
            if existing is not None and not await self._authentic(request, existing):
                return InboundResponse(401)                                   # a re-install must prove knowledge of the old secret
            secret = body.get("sharedSecret")
            if not secret:
                return InboundResponse(400)
            await self.store.put(client_key, str(secret), body.get("baseUrl"))
            return InboundResponse(204)
        if request.path.split("?")[0] == self.uninstalled_path:
            if existing is None:
                return InboundResponse(204)                                   # already gone: idempotent
            if not await self._authentic(request, existing):
                return InboundResponse(401)
            await self.store.delete(client_key)                               # uninstall wipes the secret
            return InboundResponse(204)
        return InboundResponse(404)

    @staticmethod
    async def _authentic(request: InboundRequest, secret: str) -> bool:
        class _One:
            async def shared_secret(self, client_key: str) -> str | None:
                return secret
        try:
            await ConnectJwtVerifier(_One()).verify(request)
        except Exception:
            return False
        return True


class ConnectWebhookReceiver(QueueEndpoint):
    """Connect webhook module deliveries: HS256 JWT with the tenant's secret, ``iss`` = clientKey, ``qsh`` for THIS request, ``exp`` enforced.
    Dedup key: the webhook identifier header when present, else a payload hash."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-04"})
    requires_public_ingress: ClassVar[bool] = True
    kind: ClassVar[EventSourceKind] = EventSourceKind.CONNECT_WEBHOOK

    def __init__(self, queue: LocalQueue, store: ConnectSecretStore, *, path: str = "/hooks/connect", extra_verifiers: Sequence[RequestVerifier] = (),
                 receipts: DedupStore | None = None, **kwargs: Any) -> None:
        super().__init__(queue, path=path, verifiers=(ConnectJwtVerifier(store), *extra_verifiers), receipts=receipts, **kwargs)

    def normalize(self, request: InboundRequest, payload: Mapping[str, Any]) -> ChangeEvent:
        return normalize_jira_webhook(payload, request.headers, method_id="DM-04", body=request.body, received_at=self._clock(), verified=True, source=self.kind)


def jwt_issuer(token: str) -> str | None:
    """The unverified ``iss`` of a token (used only to pick which tenant secret to verify with)."""
    try:
        import jwt
        value = jwt.decode(token, options={"verify_signature": False}).get("iss")
        return None if value is None else str(value)
    except Exception:
        return None


__all__ = ["ConnectLifecycleEndpoint", "ConnectSecretStore", "ConnectWebhookReceiver", "EncryptedFileConnectStore", "MemoryConnectStore", "jwt_issuer"]
