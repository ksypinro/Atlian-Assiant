"""Operation-keyed transport: lets the REST components run over a backend that is not HTTP (CLI, GraphQL, browser, SQL).

The Cloud REST components describe every call as a ``RequestSpec`` carrying the canonical operation id. ``OpTransport`` looks the operation up in
a handler table and returns the handler's JSON in the REST wire shape, so all parsing, normalization and pagination logic is shared. An operation with
no handler raises ``UnsupportedOperationError`` BEFORE anything is executed, which is what makes the router fall back (design 12.4).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, ClassVar

import httpx

from ..capabilities import BackendKind, Deployment
from ..core.errors import UnsupportedOperationError
from ..core.http import HttpTransport, RequestSpec
from ..core.urls import ApiSurface, UrlResolver
from ..protocols.base import BackendHealth

Handler = Callable[[RequestSpec], Awaitable[Any]]


class NoAuth:
    """These backends authenticate outside this process (a CLI login, a browser profile, a share profile): nothing is attached to requests."""

    scheme: ClassVar[str] = "external"
    uses_gateway: ClassVar[bool] = False

    def supports(self, surface: ApiSurface) -> bool:
        return True

    async def apply(self, request: httpx.Request) -> None:
        return None

    async def refresh(self) -> bool:
        return False


def _never(request: httpx.Request) -> httpx.Response:                    # pragma: no cover - guards against accidental network use
    raise AssertionError(f"OpTransport must not perform HTTP: {request.url}")


class OpTransport(HttpTransport):
    def __init__(self, backend: str, handlers: Mapping[str, Handler], *, site: str | None = None, hint: str | None = None) -> None:
        super().__init__(httpx.AsyncClient(transport=httpx.MockTransport(_never)), NoAuth(), UrlResolver(site, None, False), backend=backend)
        self.handlers, self._hint = dict(handlers), hint

    @property
    def operations(self) -> frozenset[str]:
        return frozenset(self.handlers)

    async def send(self, spec: RequestSpec) -> httpx.Response:
        op = spec.operation or ""
        handler = self.handlers.get(op)
        if handler is None:
            raise UnsupportedOperationError(f"{op or spec.path} is not available through the {self.backend} backend", operation=op or None, backend=self.backend,
                                            hint=self._hint or "The router falls back to a backend that implements it (REST or MCP).")
        out = await handler(spec)
        if isinstance(out, httpx.Response):
            return out
        if isinstance(out, (bytes, bytearray)):
            return httpx.Response(200, content=bytes(out))
        if out is None:
            return httpx.Response(204)
        return httpx.Response(200, json=out)

    async def aclose(self) -> None:
        await self.client.aclose()


class OpBackend:
    """A Backend assembled from REST components bound to an ``OpTransport``. ``supported_operations()`` is the declared (matrix) set."""

    kind: ClassVar[BackendKind]
    deployments: ClassVar[frozenset[Deployment]] = frozenset({Deployment.CLOUD})
    #: operations the shared REST components compose from other operations (no command of their own): ``op -> operations it needs``
    COMPOSED: ClassVar[dict[str, tuple[str, ...]]] = {"jira.user.lookup_account_id": ("jira.user.search",), "confluence.content.diff": ("confluence.content.get", "confluence.content.versions")}

    #: operations served by a part that does not go through the transport's handler table (e.g. the generic GraphQL executor)
    DIRECT: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, transport: OpTransport, parts: tuple[object, ...], declared: frozenset[str]) -> None:
        have = transport.operations
        declared = frozenset(o for o in declared if o in have or o in self.DIRECT or (o in self.COMPOSED and set(self.COMPOSED[o]) <= have))
        self.transport, self._parts, self._declared = transport, parts, declared
        for part in parts:
            part.kind = self.kind                                              # type: ignore[attr-defined]

    def implementation_for(self, protocol: type) -> object | None:
        return next((p for p in self._parts if isinstance(p, protocol)), None)

    def supported_operations(self) -> frozenset[str]:
        return self._declared

    async def health(self) -> BackendHealth:
        return BackendHealth("ready", None)

    async def aclose(self) -> None:
        await self.transport.aclose()
