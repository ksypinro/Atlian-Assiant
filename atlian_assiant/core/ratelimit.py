"""Rate-limit header observation and concurrency control (design section 7.6)."""
from __future__ import annotations

import contextlib
from collections import defaultdict
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field

import anyio


@dataclass
class RateLimitState:
    remaining: int | None = None
    limit: int | None = None
    reason: str | None = None
    retry_after: float | None = None


def _num(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


class RateLimitObserver:
    """Tolerant parser for Retry-After, RateLimit-Reason, X-RateLimit-* and Beta-Retry-After (header names vary)."""

    def __init__(self) -> None:
        self.state: dict[str, RateLimitState] = defaultdict(RateLimitState)

    def observe(self, surface: str, headers: Mapping[str, str]) -> RateLimitState:
        h = {k.lower(): v for k, v in headers.items()}
        st = self.state[surface]
        remaining = _num(h.get("x-ratelimit-remaining"))
        limit = _num(h.get("x-ratelimit-limit"))
        st.remaining = int(remaining) if remaining is not None else st.remaining
        st.limit = int(limit) if limit is not None else st.limit
        st.reason = h.get("ratelimit-reason", st.reason)
        st.retry_after = _num(h.get("retry-after")) or _num(h.get("beta-retry-after"))
        return st

    def retry_after(self, headers: Mapping[str, str]) -> float | None:
        h = {k.lower(): v for k, v in headers.items()}
        return _num(h.get("retry-after")) or _num(h.get("beta-retry-after"))


DEFAULT_LIMITS = {"jira": 8, "confluence": 8, "graphql": 4, "mcp": 4, "cli": 2, "browser": 1}


class ConcurrencyLimiter:
    """One semaphore per surface family."""

    def __init__(self, limits: Mapping[str, int] | None = None, default: int = 8) -> None:
        self._limits = dict(DEFAULT_LIMITS)
        self._limits.update(limits or {})
        self._default = default
        self._sems: dict[str, anyio.Semaphore] = {}

    @contextlib.asynccontextmanager
    async def limit(self, family: str) -> AsyncIterator[None]:
        sem = self._sems.setdefault(family, anyio.Semaphore(self._limits.get(family, self._default)))
        async with sem:
            yield


@dataclass
class IssueWriteSerializer:
    """Serializes writes to the same issue key to respect per-issue write limits (DA section 5.2)."""

    _locks: dict[str, anyio.Lock] = field(default_factory=dict)

    @contextlib.asynccontextmanager
    async def serialize(self, key: str | None) -> AsyncIterator[None]:
        if key is None:
            yield
            return
        lock = self._locks.setdefault(key, anyio.Lock())
        async with lock:
            yield
