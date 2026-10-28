"""RssSource (DM-30, DM-31): authenticated RSS/Atom with conditional GET (ETag / Last-Modified). Enrichment supplies canonical state."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar

import anyio
import httpx

from ...core.errors import InvalidRequestError
from ..human.parsers import rss_entry_text
from ..model import ChangeEvent, EventSourceKind
from ..normalize import AtlassianLinkParser
from ..stores.base import CheckpointStore

_ATOM = "{http://www.w3.org/2005/Atom}"


def parse_feed(xml: str) -> list[dict[str, str]]:
    """RSS 2.0 and Atom entries -> ``{guid, title, link, summary, updated}``. Raises ``InvalidRequestError`` on malformed XML."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise InvalidRequestError(f"feed is not valid XML: {exc}", hint="Check the feed URL and credentials: an HTML login page is not a feed.") from exc
    if root.tag.split("}")[-1] not in ("rss", "feed", "RDF"):
        raise InvalidRequestError(f"the response is not an RSS/Atom feed (root element <{root.tag.split('}')[-1]}>)",
                                  hint="Check the feed URL and credentials: an HTML login page is not a feed.")
    entries: list[dict[str, str]] = []
    for item in root.iter("item"):
        entries.append({"guid": (item.findtext("guid") or item.findtext("link") or "").strip(), "title": (item.findtext("title") or "").strip(),
                        "link": (item.findtext("link") or "").strip(), "summary": (item.findtext("description") or "").strip(), "updated": (item.findtext("pubDate") or "").strip()})
    for entry in root.iter(f"{_ATOM}entry"):
        link = entry.find(f"{_ATOM}link")
        entries.append({"guid": (entry.findtext(f"{_ATOM}id") or "").strip(), "title": (entry.findtext(f"{_ATOM}title") or "").strip(),
                        "link": (link.get("href") if link is not None else "") or "", "summary": (entry.findtext(f"{_ATOM}summary") or "").strip(),
                        "updated": (entry.findtext(f"{_ATOM}updated") or "").strip()})
    return entries


class RssSource:
    kind: ClassVar[EventSourceKind] = EventSourceKind.RSS
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-30", "DM-31"})
    requires_public_ingress: ClassVar[bool] = False

    def __init__(self, http: httpx.AsyncClient, url: str, checkpoints: CheckpointStore, *, headers: Mapping[str, str] | None = None, auth: tuple[str, str] | None = None,
                 project_keys: Iterable[str] = (), method_id: str = "DM-30", interval: float = 300.0, sleep: Callable[[float], Awaitable[None]] = anyio.sleep) -> None:
        self.http, self.url, self.checkpoints, self.headers, self.auth = http, url, checkpoints, dict(headers or {}), auth
        self.parser, self.method_id, self.interval, self._sleep, self._closed = AtlassianLinkParser(project_keys), method_id, interval, sleep, False
        self.stream = f"rss:{url}"
        self.not_modified = 0

    async def poll_once(self) -> list[ChangeEvent]:
        import json
        state: dict[str, Any] = json.loads(await self.checkpoints.load(self.stream) or "{}")
        headers = dict(self.headers)
        if state.get("etag"):
            headers["If-None-Match"] = state["etag"]
        if state.get("modified"):
            headers["If-Modified-Since"] = state["modified"]
        resp = await self.http.get(self.url, headers=headers, auth=self.auth) if self.auth else await self.http.get(self.url, headers=headers)
        if resp.status_code == 304:
            self.not_modified += 1
            return []
        resp.raise_for_status()
        seen = set(state.get("seen", []))
        out: list[ChangeEvent] = []
        for e in parse_feed(resp.text):
            if e["guid"] in seen:
                continue
            seen.add(e["guid"])
            out += self.parser.events(rss_entry_text(e), source=self.kind, native_type="rss:entry", message_id=e["guid"] or e["link"], method_id=self.method_id,
                                      received_at=datetime.now(UTC))
        new_state = {"etag": resp.headers.get("etag"), "modified": resp.headers.get("last-modified"), "seen": sorted(seen)[-500:]}
        await self.checkpoints.save(self.stream, json.dumps(new_state))
        return out

    async def events(self) -> AsyncIterator[ChangeEvent]:
        while not self._closed:
            for ev in await self.poll_once():
                yield ev
            if self._closed:
                break
            await self._sleep(self.interval)

    async def ack(self, event: ChangeEvent) -> None:
        return None

    async def nack(self, event: ChangeEvent, *, error: BaseException) -> None:
        return None

    async def aclose(self) -> None:
        self._closed = True
