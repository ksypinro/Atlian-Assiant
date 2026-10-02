from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from atlassian_client.models.common import Page

T = TypeVar("T")


async def drain(fetch: Callable[..., Awaitable[Page[T]]], *args: Any, limit: int = 2, **kwargs: Any) -> list[Page[T]]:
    """Follow next_cursor until exhausted; returns the pages. Fails if it never ends."""
    pages: list[Page[T]] = []
    cursor: str | None = None
    for _ in range(20):
        page = await fetch(*args, limit=limit, cursor=cursor, **kwargs)
        pages.append(page)
        if not page.next_cursor:
            return pages
        cursor = page.next_cursor
    raise AssertionError("pagination did not terminate")
