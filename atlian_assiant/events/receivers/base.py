"""Inbound webhook plumbing (design section 14.5): request/response types, verifier chain and the shared endpoint flow."""
from __future__ import annotations

import hashlib
import hmac
import inspect
import ipaddress
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar, Protocol
from urllib.parse import parse_qsl, quote, urlsplit

from ...core.errors import ConfigurationError, WebhookVerificationError
from ..model import ChangeEvent
from ..stores.base import DedupStore, LocalQueue
from ..stores.memory import MemoryDedupStore


@dataclass(frozen=True)
class InboundRequest:
    method: str
    path: str
    headers: Mapping[str, str]
    body: bytes
    query: Mapping[str, str] | None = None
    remote_addr: str | None = None

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        return next((v for k, v in self.headers.items() if k.lower() == lowered), None)


@dataclass(frozen=True)
class InboundResponse:
    status: int
    body: bytes = b""


class RequestVerifier(Protocol):
    async def verify(self, request: InboundRequest) -> None:
        """Raise WebhookVerificationError if the request is not authentic."""
        ...


class WebhookEndpoint(Protocol):
    path: str
    method_ids: ClassVar[frozenset[str]]
    verifiers: Sequence[RequestVerifier]

    async def handle(self, request: InboundRequest) -> InboundResponse:
        """verify -> parse -> normalize -> dedup check -> durable enqueue -> respond fast (EV section 7.5)."""
        ...


def _fail(reason: str) -> WebhookVerificationError:
    # deliberately generic towards the caller (the endpoint answers a bare 401); the reason is for logs and tests
    return WebhookVerificationError(f"webhook verification failed: {reason}", hint="Check the shared secret / header configuration.", status=401)


def _eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


class HmacSignatureVerifier:
    """HMAC of the raw body. Accepts ``sha256=<hex>`` or bare hex in ``header`` (WebSub style, used by Jira admin webhooks with a secret).

    The exact header and algorithm Atlassian uses are a VERIFY item (VER-01): both are configurable.
    """

    def __init__(self, secret: str | bytes, header: str = "X-Hub-Signature", algorithm: str = "sha256") -> None:
        self._secret = secret.encode() if isinstance(secret, str) else secret
        self.header, self.algorithm = header, algorithm
        if algorithm not in hashlib.algorithms_available:
            raise ConfigurationError(f"unknown hash algorithm {algorithm!r}")

    async def verify(self, request: InboundRequest) -> None:
        got = request.header(self.header)
        if not got:
            raise _fail(f"missing {self.header}")
        digest = got.split("=", 1)[1] if "=" in got else got
        expected = hmac.new(self._secret, request.body, self.algorithm).hexdigest()
        if not _eq(digest.strip().lower(), expected):
            raise _fail("bad signature")


class StaticHeaderVerifier:
    """A pre-shared header value (Automation custom headers, audit-webhook Authorization header)."""

    def __init__(self, header: str, value: str) -> None:
        self.header, self._value = header, value

    async def verify(self, request: InboundRequest) -> None:
        got = request.header(self.header)
        if got is None or not _eq(got, self._value):
            raise _fail(f"bad or missing {self.header}")


KeySource = Callable[[str | None], "Any"]    # returns a key (str, bytes or a cryptography key object), or an awaitable of one


def _decode_jwt(token: str, key: Any, algorithms: Sequence[str], **opts: Any) -> dict[str, Any]:
    try:
        import jwt
    except ImportError as exc:                                      # pragma: no cover - optional extra
        raise ConfigurationError("JWT verification needs PyJWT", hint="pip install 'atlian-assiant[jwt]'") from exc
    try:
        return dict(jwt.decode(token, key, algorithms=list(algorithms), **opts))
    except jwt.PyJWTError as exc:
        raise _fail(f"invalid token ({type(exc).__name__})") from exc


def _bearer(request: InboundRequest) -> str:
    auth = request.header("Authorization") or ""
    scheme, _, token = auth.partition(" ")
    if scheme.upper() not in ("JWT", "BEARER") or not token:
        raise _fail("missing Authorization token")
    return token


async def _key(source: KeySource, issuer: str | None) -> Any:
    key = source(issuer)
    return await key if inspect.isawaitable(key) else key


class JwtVerifier:
    """Signed-JWT webhooks (DM-03 app-registered webhooks): ``Authorization: JWT <token>``; signature, ``exp`` and optional issuer."""

    def __init__(self, issuer: str | None, key_source: KeySource, algorithms: Sequence[str] = ("HS256", "RS256")) -> None:
        self.issuer, self._keys, self.algorithms = issuer, key_source, tuple(algorithms)

    async def verify(self, request: InboundRequest) -> None:
        token = _bearer(request)
        opts: dict[str, Any] = {"issuer": self.issuer} if self.issuer else {}
        _decode_jwt(token, await _key(self._keys, self.issuer), self.algorithms, **opts)


class SharedSecretStore(Protocol):
    async def shared_secret(self, client_key: str) -> str | None: ...


def connect_qsh(method: str, path: str, query: Mapping[str, str] | None) -> str:
    """Atlassian Connect canonical request hash (same canonicalization as ``ConnectJwtAuth.query_string_hash``): ``METHOD&path&sorted-encoded-query``
    with the ``jwt`` parameter excluded and the trailing slash removed, SHA-256 hex."""
    pairs = sorted((k, v) for k, v in (query or {}).items() if k != "jwt")
    canonical_query = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in pairs)
    return hashlib.sha256(f"{method.upper()}&{path.rstrip('/') or '/'}&{canonical_query}".encode()).hexdigest()


class ConnectJwtVerifier:
    """DM-04: HS256 with the tenant's ``sharedSecret``; ``iss`` is the tenant's ``clientKey``; ``qsh`` must match this request; ``exp`` enforced."""

    def __init__(self, store: SharedSecretStore, *, check_qsh: bool = True) -> None:
        self.store, self.check_qsh = store, check_qsh

    async def verify(self, request: InboundRequest) -> None:
        token = (request.query or {}).get("jwt") or _bearer(request)
        try:
            import jwt
            unverified = jwt.decode(token, options={"verify_signature": False})
        except Exception as exc:
            raise _fail("unreadable token") from exc
        client_key = unverified.get("iss")
        secret = await self.store.shared_secret(str(client_key)) if client_key else None
        if not secret:
            raise _fail("unknown tenant")
        claims = _decode_jwt(str(token), secret, ("HS256",), options={"require": ["exp", "iss"]})
        if self.check_qsh and claims.get("qsh") not in (None, "context-qsh") and claims.get("qsh") != connect_qsh(request.method, request.path, request.query):
            raise _fail("qsh mismatch")


class ForgeInvocationTokenVerifier:
    """DM-09: the Forge Invocation Token (FIT) JWT: audience must be this app, signature from the Forge JWKS (key source injected)."""

    def __init__(self, app_id: str, jwks_url: str, key_source: KeySource) -> None:
        self.app_id, self.jwks_url, self._keys = app_id, jwks_url, key_source

    async def verify(self, request: InboundRequest) -> None:
        token = _bearer(request)
        _decode_jwt(token, await _key(self._keys, None), ("RS256",), audience=self.app_id)


def parse_ip_ranges(data: Any) -> frozenset[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Tolerant parser for an egress-range feed (the format is VER-12): list/lines of CIDRs, or JSON with ``items``/``cidr`` entries."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            found.extend(t for t in node.replace(",", " ").split() if "/" in t or t.count(".") == 3 or ":" in t)
        elif isinstance(node, Mapping):
            for k, v in node.items():
                if k in ("cidr", "range", "ip", "prefix", "ipv4_prefix", "ipv6_prefix", "ipPrefix"):
                    walk(v)
                elif isinstance(v, (list, Mapping)):
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(json.loads(data) if isinstance(data, (str, bytes)) and str(data)[:1] in "[{" else data)
    nets = []
    for token in found:
        try:
            nets.append(ipaddress.ip_network(token, strict=False))
        except ValueError:
            continue
    return frozenset(nets)


class IpAllowlistVerifier:
    """Source-address allow-list. ``refresh(loader)`` swaps in the current published egress ranges (EV sections 3.1, 54)."""

    def __init__(self, ranges: Sequence[str] = ()) -> None:
        self._nets = frozenset(ipaddress.ip_network(r, strict=False) for r in ranges)

    async def refresh(self, loader: Callable[[], Awaitable[Any]]) -> int:
        nets = parse_ip_ranges(await loader())
        if nets:                                                    # never replace a working list with an empty/unparsable feed
            self._nets = nets
        return len(self._nets)

    async def verify(self, request: InboundRequest) -> None:
        if not request.remote_addr:
            raise _fail("no remote address")
        try:
            addr = ipaddress.ip_address(request.remote_addr)
        except ValueError as exc:
            raise _fail("bad remote address") from exc
        if not any(addr in net for net in self._nets):
            raise _fail("source address not allowed")


class ReplayWindowVerifier:
    """Rejects stale requests where the payload (or a header) carries a timestamp (ms epoch or ISO-8601)."""

    def __init__(self, max_age: float = 600.0, header: str | None = None, clock: Callable[[], float] = time.time) -> None:
        self.max_age, self.header, self._clock = max_age, header, clock

    async def verify(self, request: InboundRequest) -> None:
        raw: Any = request.header(self.header) if self.header else None
        if raw is None:
            try:
                raw = json.loads(request.body).get("timestamp")
            except (ValueError, AttributeError):
                raw = None
        if raw is None:
            return                                                  # nothing to check: not every payload carries a timestamp
        try:
            ts = float(raw) / 1000 if float(raw) > 1e11 else float(raw)
        except ValueError:
            try:
                ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
            except ValueError as exc:
                raise _fail("unreadable timestamp") from exc
        if abs(self._clock() - ts) > self.max_age:
            raise _fail("stale request")


class AllOf:
    """Composition: every verifier must pass."""

    def __init__(self, *verifiers: RequestVerifier) -> None:
        self.verifiers = verifiers

    async def verify(self, request: InboundRequest) -> None:
        for v in self.verifiers:
            await v.verify(request)


@dataclass(frozen=True)
class RelayOutcome:
    """Result of verify + normalize WITHOUT any queue or dedup store (relays are stateless; dedup happens at the consumer)."""

    status: int
    event: ChangeEvent | None = None


class QueueEndpoint:
    """Shared endpoint flow: verify -> parse -> normalize -> receipt dedup -> durable enqueue -> fast response (design 14.5 table).

    | situation | status |
    | verified and durably enqueued | 202 |
    | duplicate | 200 (Atlassian stops retrying) |
    | verification failed | 401 (not in the retry set) |
    | queue unavailable | 503 (Atlassian retries) |
    | overloaded | 429 (Atlassian retries) |
    """

    path: str = "/"
    method_ids: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, queue: LocalQueue, *, path: str, verifiers: Sequence[RequestVerifier] = (), receipts: DedupStore | None = None,
                 max_body_bytes: int = 5 * 1024 * 1024, max_queue_depth: int = 10_000, clock: Callable[[], datetime] | None = None) -> None:
        self.queue, self.path = queue, path
        self.verifiers: Sequence[RequestVerifier] = tuple(verifiers)
        self.receipts = receipts or MemoryDedupStore()
        self.max_body_bytes, self.max_queue_depth = max_body_bytes, max_queue_depth
        self._clock = clock or (lambda: datetime.now(UTC))
        self.metrics: dict[str, int] = {}
        self.latency_by_flow: dict[str, list[float]] = {}

    def normalize(self, request: InboundRequest, payload: Mapping[str, Any]) -> ChangeEvent:   # pragma: no cover - abstract
        raise NotImplementedError

    def _count(self, name: str) -> None:
        self.metrics[name] = self.metrics.get(name, 0) + 1

    async def verify_and_normalize(self, request: InboundRequest) -> RelayOutcome:
        """The shared front half of ``handle``: path/method/size checks, the verifier chain, JSON parsing and normalization. 202 means 'event is valid'."""
        if urlsplit(request.path).path != self.path:
            return RelayOutcome(404)
        if request.method.upper() != "POST":
            return RelayOutcome(405)
        if len(request.body) > self.max_body_bytes:
            return RelayOutcome(413)
        try:
            for verifier in self.verifiers:
                await verifier.verify(request)
        except WebhookVerificationError:
            return RelayOutcome(401)
        try:
            payload = json.loads(request.body or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("body is not a JSON object")
        except ValueError:
            return RelayOutcome(400)
        return RelayOutcome(202, self.normalize(_with_query(request), payload))

    def verify_and_normalize_sync(self, request: InboundRequest) -> RelayOutcome:
        """Blocking variant for serverless handlers (AWS Lambda, Azure/GCP functions)."""
        import anyio
        return anyio.run(self.verify_and_normalize, request)

    async def handle(self, request: InboundRequest) -> InboundResponse:
        if urlsplit(request.path).path != self.path:
            return InboundResponse(404)
        if request.method.upper() != "POST":
            return InboundResponse(405)
        if len(request.body) > self.max_body_bytes:
            self._count("too_large")
            return InboundResponse(413)
        try:
            for verifier in self.verifiers:
                await verifier.verify(request)
        except WebhookVerificationError:
            self._count("unauthorized")
            return InboundResponse(401)
        try:
            payload = json.loads(request.body or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("body is not a JSON object")
        except ValueError:
            self._count("bad_request")
            return InboundResponse(400)                             # permanent: not in Atlassian's retry set
        request = _with_query(request)
        event = self.normalize(request, payload)
        if await self.receipts.seen(event.dedup_key):
            self._count("duplicate")
            return InboundResponse(200)
        try:
            if await self.queue.depth() >= self.max_queue_depth:
                self._count("overloaded")
                return InboundResponse(429)
            await self.queue.put(event)
        except Exception:
            self._count("queue_unavailable")
            return InboundResponse(503)
        await self.receipts.mark(event.dedup_key)
        self._count("accepted")
        flow = event.delivery.flow or "unknown"
        if event.occurred_at is not None:
            self.latency_by_flow.setdefault(flow, []).append((self._clock() - event.occurred_at).total_seconds())
        return InboundResponse(202)


def _with_query(request: InboundRequest) -> InboundRequest:
    if request.query is not None or "?" not in request.path:
        return request
    return InboundRequest(request.method, request.path, request.headers, request.body, dict(parse_qsl(urlsplit(request.path).query)), request.remote_addr)
