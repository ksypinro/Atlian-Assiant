"""CanonicalEnricher (EV sections 11, 51, 53): turn a thin event into fresh canonical state, gate stale versions, handle deletes."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import anyio

from ..core.errors import NotFoundError
from ..models.common import Tombstone
from .model import ChangeAction, ChangeEvent, EntityType
from .stores.base import VersionStore

ISSUE_FAMILY = frozenset({EntityType.ISSUE, EntityType.COMMENT, EntityType.WORKLOG, EntityType.ATTACHMENT, EntityType.ISSUE_LINK,
                          EntityType.ISSUE_PROPERTY})
CONTENT_FAMILY = frozenset({EntityType.PAGE, EntityType.BLOGPOST, EntityType.WHITEBOARD, EntityType.DATABASE, EntityType.FOLDER})


class _Flight:
    def __init__(self) -> None:
        self.done = anyio.Event()
        self.result: Any = None
        self.error: BaseException | None = None


@dataclass
class EnrichStats:
    fetched: int = 0
    coalesced: int = 0
    stale: int = 0
    tombstones: int = 0
    subtree_checks: list[tuple[str, ...]] = field(default_factory=list)


def _newer(new: str, old: str | None) -> bool:
    """True when ``new`` is strictly newer than ``old``: integer versions, ISO timestamps, else any change."""
    if old is None:
        return True
    try:
        return int(new) > int(old)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(new.replace("Z", "+00:00")) > datetime.fromisoformat(old.replace("Z", "+00:00"))
    except ValueError:
        return new != old


def entity_ref(event: ChangeEvent) -> str | None:
    """Stable reference used by the version store, e.g. ``issue:PROJ-1`` or ``page:123``."""
    if event.entity_type in ISSUE_FAMILY:
        return f"issue:{event.entity_key}" if event.entity_key else None
    if event.entity_type in CONTENT_FAMILY:
        return f"{event.entity_type.value}:{event.entity_id}" if event.entity_id else None
    return None


class CanonicalEnricher:
    """Resolves events to fresh REST state. The webhook payload is never the source of truth (EV section 51).

    * entity -> read operation (issue family -> ``get_issue``; page/blog/... -> ``get_content``)
    * bursts for one entity share a single fetch
    * a 404 becomes a ``Tombstone``; a Confluence delete invalidates known descendants and records a subtree check
    * version gate: an event whose fetched version is not newer than the stored one is dropped (``enrich`` returns ``None``)
    """

    def __init__(self, fetch_issue: Callable[[str], Awaitable[Any]], fetch_content: Callable[[str, str], Awaitable[Any]],
                 versions: VersionStore) -> None:
        self._fetch_issue, self._fetch_content, self.versions = fetch_issue, fetch_content, versions
        self.stats = EnrichStats()
        self._inflight: dict[str, _Flight] = {}

    @classmethod
    def from_client(cls, client: Any, versions: VersionStore) -> CanonicalEnricher:
        """Build from an ``AsyncAtlassianClient`` (reads go through its router, so policy and fallbacks apply)."""
        return cls(lambda key: client.jira.issues.get(key),
                   lambda cid, kind: client.confluence.content.get(cid, type=kind), versions)

    async def _fetch(self, event: ChangeEvent, ref: str) -> Any:
        flight = self._inflight.get(ref)
        if flight is not None:                                     # coalesce: a fetch for this entity is already running
            self.stats.coalesced += 1
            await flight.done.wait()
            if flight.error is not None:
                raise flight.error
            return flight.result
        flight = self._inflight[ref] = _Flight()
        try:
            self.stats.fetched += 1
            if event.entity_type in ISSUE_FAMILY:
                flight.result = await self._fetch_issue(event.entity_key or "")
            else:
                kind = "page" if event.entity_type is EntityType.PAGE else event.entity_type.value
                flight.result = await self._fetch_content(event.entity_id or "", kind)
            return flight.result
        except BaseException as exc:                               # shared with coalesced waiters, then re-raised
            flight.error = exc
            raise
        finally:
            flight.done.set()
            del self._inflight[ref]

    async def enrich(self, event: ChangeEvent) -> ChangeEvent | None:
        ref = entity_ref(event)
        if ref is None:
            return event                                           # config/user/sprint/audit/unknown: nothing to resolve
        try:
            canonical = await self._fetch(event, ref)
        except NotFoundError:
            return await self._tombstone(event, ref)
        version = self._version_of(event, canonical)
        if version:
            if not _newer(version, await self.versions.get(ref)):
                self.stats.stale += 1
                return None                                        # out-of-order / duplicate delivery: harmless
            await self.versions.put(ref, version)
        parent = getattr(canonical, "parent_key" if event.entity_type in ISSUE_FAMILY else "parent_id", None)
        if parent:
            kind = "issue" if event.entity_type in ISSUE_FAMILY else event.entity_type.value
            await self.versions.link(f"{kind}:{parent}", ref)
        return event.model_copy(update={"canonical": canonical, "entity_version": version or event.entity_version})

    @staticmethod
    def _version_of(event: ChangeEvent, canonical: Any) -> str:
        if event.entity_type in ISSUE_FAMILY:
            updated = getattr(canonical, "updated", None)
            return updated.isoformat() if updated is not None else ""
        version = getattr(canonical, "version", None)
        return "" if version is None else str(version)

    async def _tombstone(self, event: ChangeEvent, ref: str) -> ChangeEvent:
        dropped = await self.versions.invalidate(ref)              # includes `ref` itself, then every known descendant
        descendants = tuple(d for d in dropped if d != ref)
        if event.entity_type in CONTENT_FAMILY:                    # EV section 11: no per-child delete events are guaranteed
            self.stats.subtree_checks.append((ref, *descendants))
        await self.versions.put(ref, "tombstone")
        self.stats.tombstones += 1
        stone = Tombstone(entity_type=event.entity_type.value, entity_id=event.entity_key or event.entity_id or ref,
                          reason="not_found" + (f"; invalidated {len(descendants)} descendant(s)" if descendants else ""))
        action = event.action if event.action in (ChangeAction.DELETED, ChangeAction.SOFT_DELETED) else ChangeAction.DELETED
        return event.model_copy(update={"canonical": stone, "action": action})
