"""TTL metadata cache keyed by (cloudId, surface, path) (design section 7.8)."""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")


class MetadataCache:
    """Small async-friendly TTL cache. Fields, projects, statuses, issue types and spaces change rarely."""

    def __init__(self, ttl: float = 300.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl, self._clock = ttl, clock
        self._data: dict[tuple[str, ...], tuple[float, Any]] = {}

    def get(self, *key: str) -> Any | None:
        hit = self._data.get(key)
        if hit is None:
            return None
        stamp, value = hit
        if self._clock() - stamp > self.ttl:
            del self._data[key]
            return None
        return value

    def set(self, value: Any, *key: str) -> None:
        self._data[key] = (self._clock(), value)

    async def get_or_load(self, loader: Callable[[], Awaitable[T]], *key: str) -> T:
        cached = self.get(*key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        value = await loader()
        self.set(value, *key)
        return value

    def invalidate(self, *prefix: str) -> int:
        doomed = [k for k in self._data if k[: len(prefix)] == prefix]
        for k in doomed:
            del self._data[k]
        return len(doomed)
