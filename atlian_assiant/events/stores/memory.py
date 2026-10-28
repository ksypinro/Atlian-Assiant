"""In-memory stores: the default for tests and single-process use."""
from __future__ import annotations

import itertools
import time
from collections import OrderedDict, deque

from ..model import ChangeEvent


class MemoryCheckpointStore:
    def __init__(self) -> None:
        self._v: dict[str, str] = {}

    async def load(self, stream: str) -> str | None:
        return self._v.get(stream)

    async def save(self, stream: str, value: str) -> None:
        self._v[stream] = value


class MemoryDedupStore:
    """TTL LRU set."""

    def __init__(self, max_entries: int = 100_000) -> None:
        self._v: OrderedDict[str, float] = OrderedDict()
        self._max = max_entries

    async def seen(self, key: str) -> bool:
        exp = self._v.get(key)
        if exp is None:
            return False
        if exp < time.monotonic():
            del self._v[key]
            return False
        return True

    async def mark(self, key: str, ttl: float = 7 * 86400) -> None:
        self._v[key] = time.monotonic() + ttl
        self._v.move_to_end(key)
        while len(self._v) > self._max:
            self._v.popitem(last=False)


class MemoryLocalQueue:
    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self._ready: deque[tuple[str, ChangeEvent]] = deque()
        self._inflight: dict[str, ChangeEvent] = {}

    async def put(self, event: ChangeEvent) -> str:
        qid = str(next(self._ids))
        self._ready.append((qid, event))
        return qid

    async def get(self) -> tuple[str, ChangeEvent] | None:
        if not self._ready:
            return None
        qid, event = self._ready.popleft()
        self._inflight[qid] = event
        return qid, event

    async def ack(self, queue_id: str) -> None:
        self._inflight.pop(queue_id, None)

    async def depth(self) -> int:
        return len(self._ready) + len(self._inflight)


class MemoryDeadLetterSink:
    def __init__(self) -> None:
        self.items: list[tuple[ChangeEvent, str]] = []

    async def put(self, event: ChangeEvent, error: BaseException) -> None:
        self.items.append((event, f"{type(error).__name__}: {error}"))


class MemoryVersionStore:
    """Entity versions plus the parent -> children map used for cascade invalidation (CC-05)."""

    def __init__(self) -> None:
        self._versions: dict[str, str] = {}
        self._children: dict[str, set[str]] = {}

    async def get(self, entity: str) -> str | None:
        return self._versions.get(entity)

    async def put(self, entity: str, version: str) -> None:
        self._versions[entity] = version

    async def children(self, entity: str) -> list[str]:
        return sorted(self._children.get(entity, ()))

    async def link(self, parent: str, child: str) -> None:
        self._children.setdefault(parent, set()).add(child)

    async def invalidate(self, entity: str) -> list[str]:
        """Forget ``entity`` and all known descendants; returns the invalidated ids (entity first)."""
        out, stack = [], [entity]
        while stack:
            current = stack.pop()
            if current in out:
                continue
            out.append(current)
            self._versions.pop(current, None)
            stack.extend(self._children.pop(current, ()))
        return out
