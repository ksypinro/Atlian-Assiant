"""Shared plumbing for the Cloud REST mixins."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

from ...capabilities import BackendKind
from ...core.http import HttpTransport, RequestSpec
from ...core.pagination import Cursor, PaginationStrategy
from ...core.urls import ApiSurface
from ...models.common import Page

T = TypeVar("T")


class RestBase:
    """Holds the transport and implements cursor-based paging once for every endpoint."""

    kind = BackendKind.REST

    def __init__(self, transport: HttpTransport) -> None:
        self.transport = transport

    @property
    def site_url(self) -> str | None:
        return f"https://{self.transport.urls.site}" if self.transport.urls.site else None

    async def _json(self, surface: ApiSurface, path: str, *, method: str = "GET", params: Mapping[str, Any] | None = None,
                    json: Any = None, headers: Mapping[str, str] | None = None, operation: str | None = None,
                    idempotent: bool = True) -> Any:
        return await self.transport.json(RequestSpec(method, surface, path, params or {}, json=json, headers=headers or {},
                                                     operation=operation, idempotent=idempotent))

    async def _page(self, spec: RequestSpec, strategy: PaginationStrategy, mapper: Callable[[Any], T], *, limit: int,
                    cursor: str | None) -> Page[T]:
        spec = strategy.first(spec, limit)
        if cursor:
            c = Cursor.decode(cursor)
            if c.strategy != strategy.name:
                from ...core.errors import InvalidRequestError

                raise InvalidRequestError("cursor does not belong to this operation",
                                          hint="Pass `next_cursor` only to the operation that returned it.")
            spec = strategy.next(spec, c.state)
        resp = await self.transport.send(spec)
        items, nxt, total = strategy.parse(resp, spec)
        return Page(items=[mapper(i) for i in items],
                    next_cursor=Cursor(self.kind, strategy.name, nxt).encode() if nxt else None, total=total)


def csv(values: Sequence[str] | None) -> str | None:
    return ",".join(values) if values else None
