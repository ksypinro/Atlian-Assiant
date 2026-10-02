"""Pagination strategies and the opaque cursor codec (design section 7.4).

There is no universal paginator: each endpoint declares its strategy and callers only ever see ``Page[T]`` with an
opaque ``next_cursor`` that pins the backend and strategy that produced it.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field, replace
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlsplit

import httpx

from ..capabilities import BackendKind
from .errors import InvalidRequestError
from .http import RequestSpec


@dataclass(frozen=True)
class Cursor:
    """Opaque to callers. Pins the backend and strategy that produced it."""

    backend: BackendKind
    strategy: str
    state: dict[str, Any]

    def encode(self) -> str:
        raw = json.dumps({"b": self.backend.value, "s": self.strategy, "x": self.state}, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode()

    @classmethod
    def decode(cls, token: str) -> Cursor:
        try:
            data = json.loads(base64.urlsafe_b64decode(token.encode()))
            return cls(BackendKind(data["b"]), str(data["s"]), dict(data["x"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise InvalidRequestError("invalid pagination cursor",
                                      hint="Pass `next_cursor` back exactly as returned; never edit or build cursors.") from exc


ParsedPage = tuple[list[Any], dict[str, Any] | None, int | None]


class PaginationStrategy(Protocol):
    name: str

    def first(self, spec: RequestSpec, limit: int) -> RequestSpec: ...

    def parse(self, response: httpx.Response, spec: RequestSpec) -> ParsedPage:
        """Return (items, next_state or None, total if reported)."""
        ...

    def next(self, spec: RequestSpec, state: dict[str, Any]) -> RequestSpec: ...


def _with(spec: RequestSpec, **params: Any) -> RequestSpec:
    return replace(spec, params={**spec.params, **params})


@dataclass
class OffsetPagination:
    """Jira classic list endpoints and Agile: startAt / maxResults, stopping on isLast or total."""

    items_key: str | None = "values"
    name: str = "offset"
    start_param: str = "startAt"
    limit_param: str = "maxResults"

    def first(self, spec: RequestSpec, limit: int) -> RequestSpec:
        return _with(spec, **{self.start_param: 0, self.limit_param: limit})

    def parse(self, response: httpx.Response, spec: RequestSpec) -> ParsedPage:
        body = response.json()
        items = body if self.items_key is None else body.get(self.items_key, [])
        if isinstance(body, list):
            start = int(spec.params.get(self.start_param, 0))
            limit = int(spec.params.get(self.limit_param, len(items) or 1))
            nxt = {"start": start + len(items)} if len(items) >= limit and items else None
            return items, nxt, None
        start = int(body.get(self.start_param, spec.params.get(self.start_param, 0)))
        total = body.get("total")
        is_last = body.get("isLast")
        done = is_last is True or not items or (total is not None and start + len(items) >= int(total))
        return list(items), None if done else {"start": start + len(items)}, int(total) if total is not None else None

    def next(self, spec: RequestSpec, state: dict[str, Any]) -> RequestSpec:
        return _with(spec, **{self.start_param: state["start"]})


@dataclass
class NextPageTokenPagination:
    """Enhanced JQL search: pass ``nextPageToken`` back; no total (DA 3.1)."""

    items_key: str = "issues"
    name: str = "next_page_token"

    def first(self, spec: RequestSpec, limit: int) -> RequestSpec:
        if isinstance(spec.json, dict):
            return replace(spec, json={**spec.json, "maxResults": limit})
        return _with(spec, maxResults=limit)

    def parse(self, response: httpx.Response, spec: RequestSpec) -> ParsedPage:
        body = response.json()
        token = body.get("nextPageToken")
        return list(body.get(self.items_key, [])), ({"token": token} if token and not body.get("isLast") else None), None

    def next(self, spec: RequestSpec, state: dict[str, Any]) -> RequestSpec:
        if isinstance(spec.json, dict):
            return replace(spec, json={**spec.json, "nextPageToken": state["token"]})
        return _with(spec, nextPageToken=state["token"])


@dataclass
class JsmPagination:
    """``/rest/servicedeskapi``: start / limit / isLastPage."""

    name: str = "jsm"

    def first(self, spec: RequestSpec, limit: int) -> RequestSpec:
        return _with(spec, start=0, limit=limit)

    def parse(self, response: httpx.Response, spec: RequestSpec) -> ParsedPage:
        body = response.json()
        items = list(body.get("values", []))
        start, size = int(body.get("start", 0)), int(body.get("size", len(items)))
        return items, None if body.get("isLastPage", True) else {"start": start + size}, None

    def next(self, spec: RequestSpec, state: dict[str, Any]) -> RequestSpec:
        return _with(spec, start=state["start"])


@dataclass
class LinkCursorPagination:
    """Confluence v2: follow ``_links.next`` (or the Link header) verbatim; never edit the cursor (DA 4.1)."""

    name: str = "link_cursor"

    def first(self, spec: RequestSpec, limit: int) -> RequestSpec:
        return _with(spec, limit=limit)

    def parse(self, response: httpx.Response, spec: RequestSpec) -> ParsedPage:
        body = response.json()
        nxt = (body.get("_links") or {}).get("next")
        if not nxt:
            link = response.headers.get("Link", "")
            if 'rel="next"' in link:
                nxt = link.split(";")[0].strip("<> ")
        return list(body.get("results", [])), ({"next": nxt} if nxt else None), None

    def next(self, spec: RequestSpec, state: dict[str, Any]) -> RequestSpec:
        # The next link is relative to the site (it already contains the /wiki prefix) and carries the opaque cursor.
        parts = urlsplit(state["next"])
        return replace(spec, path=parts.path.removeprefix("/wiki/api/v2"), params=dict(parse_qsl(parts.query)))


@dataclass
class CqlPagination:
    """Confluence v1 search: start / limit + ``_links.next``; body expansion can shrink pages, so keep reading."""

    name: str = "cql"

    def first(self, spec: RequestSpec, limit: int) -> RequestSpec:
        return _with(spec, start=0, limit=limit)

    def parse(self, response: httpx.Response, spec: RequestSpec) -> ParsedPage:
        body = response.json()
        results = list(body.get("results", []))
        start = int(body.get("start", spec.params.get("start", 0)))
        has_next = bool((body.get("_links") or {}).get("next")) and bool(results)
        total = body.get("totalSize")
        return results, {"start": start + len(results)} if has_next else None, int(total) if total is not None else None

    def next(self, spec: RequestSpec, state: dict[str, Any]) -> RequestSpec:
        return _with(spec, start=state["start"])


@dataclass
class OrgEventsCursorPagination:
    """Org ``/events`` and ``/events-stream``: cursor from ``meta.next``; the last page still returns a cursor."""

    name: str = "org_events"

    def first(self, spec: RequestSpec, limit: int) -> RequestSpec:
        return _with(spec, limit=min(limit, 500))

    def parse(self, response: httpx.Response, spec: RequestSpec) -> ParsedPage:
        body = response.json()
        meta = body.get("meta") or {}
        nxt = meta.get("next")
        items = list(body.get("data", []))
        # Keep the cursor even when the page is empty so a poller can continue from it.
        return items, {"cursor": nxt} if nxt else None, None

    def next(self, spec: RequestSpec, state: dict[str, Any]) -> RequestSpec:
        return _with(spec, cursor=state["cursor"])


@dataclass
class DcOffsetPagination(OffsetPagination):
    """Data Center: startAt/maxResults (Jira) or start/limit (Confluence)."""

    name: str = "dc_offset"


@dataclass
class CliPagination:
    """Delegated to the CLI (ACLI ``--paginate``, jira-cli paging flags): a single call returns everything requested."""

    name: str = "cli"

    def first(self, spec: RequestSpec, limit: int) -> RequestSpec:
        return _with(spec, limit=limit)

    def parse(self, response: httpx.Response, spec: RequestSpec) -> ParsedPage:
        return list(response.json()), None, None

    def next(self, spec: RequestSpec, state: dict[str, Any]) -> RequestSpec:
        return spec


@dataclass
class McpPagination:
    """MCP tools: tool-specific cursor or startAt arguments, mapped per tool."""

    cursor_key: str = "nextCursor"
    items_key: str = "items"
    name: str = "mcp"
    extra: dict[str, Any] = field(default_factory=dict)

    def first(self, spec: RequestSpec, limit: int) -> RequestSpec:
        return _with(spec, limit=limit)

    def parse(self, response: httpx.Response, spec: RequestSpec) -> ParsedPage:
        body = response.json()
        nxt = body.get(self.cursor_key)
        return list(body.get(self.items_key, [])), ({"cursor": nxt} if nxt else None), None

    def next(self, spec: RequestSpec, state: dict[str, Any]) -> RequestSpec:
        return _with(spec, cursor=state["cursor"])


STRATEGIES: dict[str, type[Any]] = {
    "offset": OffsetPagination, "next_page_token": NextPageTokenPagination, "jsm": JsmPagination,
    "link_cursor": LinkCursorPagination, "cql": CqlPagination, "org_events": OrgEventsCursorPagination,
    "dc_offset": DcOffsetPagination, "cli": CliPagination, "mcp": McpPagination,
}
