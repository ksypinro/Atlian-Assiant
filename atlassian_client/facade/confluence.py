"""Confluence facade namespaces (design section 13.1)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ..models.confluence import Content, SearchHit
from ..protocols.confluence import BodyFormat, ContentType
from ..routing import Router
from ._generated_confluence import (
    ConfluenceAttachmentsClient,
    ConfluenceCommentsClient,
    ConfluenceContentClient,
    ConfluenceLabelsClient,
    ConfluencePermissionsClient,
    ConfluencePropertiesClient,
    ConfluenceSpacesClient,
    ConfluenceTasksClient,
    ConfluenceTemplatesClient,
)
from ._generated_confluence import ConfluenceSearchClient as _SearchBase
from ._namespace import Namespace
from ._unavailable import Unavailable


class ConfluenceSearchClient(_SearchBase):
    async def iter_cql(self, cql: str, *, page_size: int = 50, max_items: int | None = None) -> AsyncIterator[SearchHit]:
        """Iterate ALL hits of a CQL query across pages (for programs; agents should use `cql`).

        When to use:
            Programs that need every match; hydrate bodies afterwards with `content.get` in controlled batches.

        Args:
            cql: CQL query.
            page_size: Hits per request.
            max_items: Stop after this many hits.

        Returns:
            An async iterator of SearchHit.

        Raises:
            InvalidRequestError: The CQL is invalid.

        Access: SEARCH

        Example:
            >>> [h.content_id async for h in client.confluence.search.iter_cql("space = ENG")]
        """
        cursor: str | None = None
        seen = 0
        while True:
            page = await self.cql(cql, limit=page_size, cursor=cursor)
            for hit in page.items:
                yield hit
                seen += 1
                if max_items is not None and seen >= max_items:
                    return
            if not page.has_more:
                return
            cursor = page.next_cursor

    async def search_then_hydrate(self, cql: str, *, limit: int = 25, body_format: BodyFormat = "markdown",
                                  content: ConfluenceContentClient | None = None) -> list[Content]:
        """Search by CQL, then fetch full bodies of the hits in a controlled batch (DA 4.2: body expansion shrinks CQL pages).

        When to use:
            You need the text of the pages that match a CQL query.

        Args:
            cql: CQL query.
            limit: Maximum pages to hydrate.
            body_format: Body format of the hydrated pages.
            content: Content client to use (defaults to a client on the same router).

        Returns:
            The hydrated Content objects, in hit order.

        Raises:
            InvalidRequestError: The CQL is invalid.

        Access: SEARCH

        Example:
            >>> pages = await client.confluence.search.search_then_hydrate('text ~ "runbook"', limit=5)
        """
        reader = content or ConfluenceContentClient(self._router)
        page = await self.cql(cql, limit=limit, excerpt=False)
        out: list[Content] = []
        for hit in page.items:
            if hit.content_id:
                out.append(await reader.get(hit.content_id, body_format=body_format))
        return out


class TypedContentClient(Namespace):
    """Shortcut bound to one content type (pages, blogposts, whiteboards, databases, folders)."""

    def __init__(self, router: Router, content_type: ContentType) -> None:
        super().__init__(router)
        self._type = content_type
        self._content = ConfluenceContentClient(router)

    async def get(self, content_id: str, *, body_format: BodyFormat = "markdown", backend: Any = None) -> Content:
        """Fetch one item of this content type by id (same as `content.get` with `type` fixed).

        When to use:
            You know the id and the type.

        Args:
            content_id: Confluence content id.
            body_format: "markdown" (default), "storage", "atlas_doc_format", "view" or "none".
            backend: Force one backend.

        Returns:
            Content

        Raises:
            NotFoundError: Missing, or you lack view permission.

        Access: READ

        Example:
            >>> await client.confluence.pages.get("123456")
        """
        return await self._content.get(content_id, type=self._type, body_format=body_format, backend=backend)


class ConfluenceClient(Namespace):
    """Confluence Cloud."""

    def __init__(self, router: Router) -> None:
        super().__init__(router)
        self.content = ConfluenceContentClient(router)
        self.pages = TypedContentClient(router, ContentType.PAGE)
        self.blogposts = TypedContentClient(router, ContentType.BLOGPOST)
        self.whiteboards = TypedContentClient(router, ContentType.WHITEBOARD)
        self.databases = TypedContentClient(router, ContentType.DATABASE)
        self.folders = TypedContentClient(router, ContentType.FOLDER)
        self.search = ConfluenceSearchClient(router)
        self.spaces = ConfluenceSpacesClient(router)
        self.comments = ConfluenceCommentsClient(router)
        self.attachments = ConfluenceAttachmentsClient(router)
        self.labels = ConfluenceLabelsClient(router)
        self.properties = ConfluencePropertiesClient(router)
        self.tasks = ConfluenceTasksClient(router)
        self.permissions = ConfluencePermissionsClient(router)
        self.templates = ConfluenceTemplatesClient(router)
        self.social: Any = Unavailable("confluence.social", "P2")
        self.special: Any = Unavailable("confluence.special (MCP-only features)", "P1")
        self.api: Any = Unavailable("confluence.api (generated REST tier)", "P6")
