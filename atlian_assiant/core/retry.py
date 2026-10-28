"""Retry policy (design section 7.6)."""
from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with jitter; ``Retry-After`` always wins.

    Non-idempotent requests are only retried on 429, because a 429 means the request was not processed.
    """

    max_attempts: int = 6
    base_delay: float = 1.0
    max_delay: float = 60.0
    retry_statuses: frozenset[int] = frozenset({429, 502, 503, 504})
    retry_non_idempotent_on: frozenset[int] = frozenset({429})

    def should_retry(self, status: int, *, idempotent: bool, attempt: int) -> bool:
        if attempt + 1 >= self.max_attempts:
            return False
        if status not in self.retry_statuses:
            return False
        return idempotent or status in self.retry_non_idempotent_on

    def delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return retry_after + random.uniform(0, 1.0)
        return float(min(self.max_delay, self.base_delay * 2**attempt) * random.uniform(0.5, 1.0))
