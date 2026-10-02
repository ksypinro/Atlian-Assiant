"""Search-then-hydrate (design sections 4.2 and 10.1).

CQL body expansion shrinks the effective search page size, so large extraction searches for IDs first and then fetches
bodies through the v2 API in controlled, bounded-concurrency batches.
"""
from __future__ import annotations

import anyio

from ...models.confluence import Content
from ...protocols.confluence import BodyFormat
from .confluence import ConfluenceRest


async def search_then_hydrate(confluence: ConfluenceRest, cql: str, *, limit: int = 100, batch_size: int = 25,
                              concurrency: int = 4, body_format: BodyFormat = "markdown") -> list[Content]:
    """Return up to ``limit`` hydrated pages for ``cql`` in hit order, never asking CQL to expand bodies."""
    ids: list[str] = []
    cursor: str | None = None
    while len(ids) < limit:
        page = await confluence.search_cql(cql, limit=min(batch_size, limit - len(ids)), cursor=cursor, excerpt=False)
        ids += [h.content_id for h in page.items if h.content_id]
        if not page.next_cursor:
            break
        cursor = page.next_cursor
    results: dict[str, Content] = {}
    limiter = anyio.CapacityLimiter(concurrency)

    async def fetch(content_id: str) -> None:
        async with limiter:
            results[content_id] = await confluence.get_content(content_id, body_format=body_format)

    for start in range(0, len(ids), batch_size):          # batches bound memory and request bursts
        async with anyio.create_task_group() as tg:
            for cid in ids[start:start + batch_size]:
                tg.start_soon(fetch, cid)
    return [results[i] for i in ids if i in results]
