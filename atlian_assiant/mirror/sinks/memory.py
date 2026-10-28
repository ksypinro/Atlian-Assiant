"""In-memory MirrorStore: the reference implementation every other sink must behave like (see tests/mirror)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


class InMemoryMirrorStore:
    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], dict[str, Any]] = {}
        self.versions: dict[tuple[str, str], str] = {}
        self.deleted: set[tuple[str, str]] = set()
        self.writes = 0

    async def get_version(self, kind: str, entity_id: str) -> str | None:
        key = (kind, entity_id)
        return None if key in self.deleted else self.versions.get(key)

    async def upsert(self, kind: str, entity_id: str, version: str, document: dict[str, Any]) -> None:
        self.docs[(kind, entity_id)], self.versions[(kind, entity_id)] = dict(document), version
        self.deleted.discard((kind, entity_id))
        self.writes += 1

    async def tombstone(self, kind: str, entity_id: str) -> None:
        if (kind, entity_id) in self.docs:
            self.deleted.add((kind, entity_id))
            self.writes += 1

    async def known_ids(self, kind: str, scope: str) -> AsyncIterator[str]:
        for (k, i), doc in sorted(self.docs.items()):
            if k == kind and doc.get("_scope") == scope and (k, i) not in self.deleted:
                yield i

    async def descendants(self, kind: str, entity_id: str) -> list[str]:
        out: list[str] = []
        frontier = [f"{kind}:{entity_id}"]
        while frontier:
            parent = frontier.pop()
            for (k, i), doc in sorted(self.docs.items()):
                if doc.get("_parent") == parent and (k, i) not in self.deleted and i not in out:
                    out.append(i)
                    frontier.append(f"{k}:{i}")
        return out

    def get(self, kind: str, entity_id: str) -> dict[str, Any] | None:
        return None if (kind, entity_id) in self.deleted else self.docs.get((kind, entity_id))
