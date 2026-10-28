"""Mirror / sync engine (design 15): the ``MirrorStore`` protocol every sink implements, plus the adapter that lets the event pipeline share its version gate with it.

A mirror document is a plain dict. Four keys are reserved for the engine: ``_scope`` (which mirror owns it, used by the nightly id-set diff), ``_parent`` (kind:id of the
parent, used for subtree tombstones), ``_deleted`` (tombstone marker) and ``_synced_at``. Everything else is the entity.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

TOMBSTONE = "tombstone"


@runtime_checkable
class MirrorStore(Protocol):
    async def get_version(self, kind: str, entity_id: str) -> str | None: ...
    async def upsert(self, kind: str, entity_id: str, version: str, document: dict[str, Any]) -> None: ...
    async def tombstone(self, kind: str, entity_id: str) -> None: ...
    def known_ids(self, kind: str, scope: str) -> AsyncIterator[str]: ...
    async def descendants(self, kind: str, entity_id: str) -> list[str]: ...


def split_ref(ref: str) -> tuple[str, str]:
    kind, _, ident = ref.partition(":")
    return kind, ident


def newer(candidate: str, current: str | None) -> bool:
    """Is ``candidate`` strictly newer than ``current``? Numeric versions compare as numbers, ISO timestamps lexicographically (they sort), anything else by inequality."""
    if current is None or current == TOMBSTONE:
        return True
    if candidate.isdigit() and current.isdigit():
        return int(candidate) > int(current)
    return candidate > current if candidate[:2].isdigit() and current[:2].isdigit() else candidate != current


class MirrorVersionStore:
    """Adapts a ``MirrorStore`` to the event pipeline's ``VersionStore`` (``kind:id`` refs), so webhook dedup and the mirror agree on what is current."""

    def __init__(self, store: MirrorStore) -> None:
        self.store = store

    async def get(self, entity: str) -> str | None:
        return await self.store.get_version(*split_ref(entity))

    async def put(self, entity: str, version: str) -> None:
        kind, ident = split_ref(entity)
        if version == TOMBSTONE:
            await self.store.tombstone(kind, ident)

    async def children(self, entity: str) -> list[str]:
        kind, ident = split_ref(entity)
        return [f"{kind}:{d}" for d in await self.store.descendants(kind, ident)]

    async def link(self, parent: str, child: str) -> None:
        return None                                             # parentage is recorded in the documents (`_parent`), not separately

    async def invalidate(self, entity: str) -> list[str]:
        kind, ident = split_ref(entity)
        gone = [entity, *(f"{kind}:{d}" for d in await self.store.descendants(kind, ident))]
        for ref in gone:
            await self.store.tombstone(*split_ref(ref))
        return gone
