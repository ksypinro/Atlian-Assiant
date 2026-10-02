"""Confluence Cloud REST reads: v2 for content, v1 for CQL search and restrictions (design sections 4, 9.5)."""
from __future__ import annotations

import difflib
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from ...core.errors import InvalidRequestError, NotFoundError, UnsupportedOperationError
from ...core.http import RequestSpec
from ...core.pagination import CqlPagination, LinkCursorPagination
from ...core.urls import ApiSurface
from ...formats.adf import adf_to_markdown
from ...formats.storage import storage_to_markdown
from ...models.common import Page
from ...models.confluence import (
    Attachment,
    AttachmentContent,
    ConfluenceComment,
    Content,
    ContentDiff,
    ContentVersion,
    Label,
    Permissions,
    Restrictions,
    SearchHit,
    Space,
    Task,
    Template,
)
from ...protocols.confluence import BodyFormat, ContentType
from . import normalize as n
from .base import RestBase

V2 = ApiSurface.CONFLUENCE_V2
V1 = ApiSurface.CONFLUENCE_V1

_COLLECTION = {ContentType.PAGE: "pages", ContentType.BLOGPOST: "blogposts", ContentType.WHITEBOARD: "whiteboards",
               ContentType.DATABASE: "databases", ContentType.FOLDER: "folders", ContentType.SMART_LINK: "embeds",
               ContentType.CUSTOM: "custom-content"}
_WIRE_BODY = {"markdown": "storage", "storage": "storage", "atlas_doc_format": "atlas_doc_format", "view": "view"}


def _body_params(body_format: BodyFormat) -> dict[str, Any]:
    return {"body-format": _WIRE_BODY[body_format]} if body_format != "none" else {}


class ConfluenceRest(RestBase):
    """Implements ConfluenceContentReader, ConfluenceSearcher, ConfluenceSpaceReader, ConfluenceTemplateReader."""

    @property
    def _wiki_base(self) -> str | None:
        return f"{self.site_url}/wiki" if self.site_url else None

    def _content(self, d: dict[str, Any], type_: ContentType = ContentType.PAGE) -> Content:
        return n.content(d, type_=type_.value, base=self._wiki_base)

    # ---------------------------------------------------------------- content
    async def get_content(self, content_id: str, *, type: ContentType = ContentType.PAGE, body_format: BodyFormat = "markdown",
                          version: int | None = None) -> Content:
        params = {**_body_params(body_format), "version": version}
        data = await self._json(V2, f"/{_COLLECTION[type]}/{content_id}", params=params, operation="confluence.content.get")
        return self._content(data, type)

    async def list_content(self, *, space_id: str | None = None, type: ContentType = ContentType.PAGE, status: str = "current",
                           title: str | None = None, limit: int = 25, cursor: str | None = None) -> Page[Content]:
        path = f"/spaces/{space_id}/{_COLLECTION[type]}" if space_id else f"/{_COLLECTION[type]}"
        spec = RequestSpec("GET", V2, path, params={"status": status, "title": title}, operation="confluence.content.list")
        return await self._page(spec, LinkCursorPagination(), lambda d: self._content(d, type), limit=limit, cursor=cursor)

    async def list_children(self, content_id: str, *, type: ContentType = ContentType.PAGE, limit: int = 25,
                            cursor: str | None = None) -> Page[Content]:
        spec = RequestSpec("GET", V2, f"/{_COLLECTION[type]}/{content_id}/children", operation="confluence.content.children")
        return await self._page(spec, LinkCursorPagination(), lambda d: self._content(d, type), limit=limit, cursor=cursor)

    async def list_ancestors(self, content_id: str, *, type: ContentType = ContentType.PAGE) -> list[Content]:
        data = await self._json(V2, f"/{_COLLECTION[type]}/{content_id}/ancestors", operation="confluence.content.ancestors")
        return [self._content(d, type) for d in data.get("results", [])]

    async def list_descendants(self, content_id: str, *, depth: int | None = None, limit: int = 50,
                               cursor: str | None = None) -> Page[Content]:
        spec = RequestSpec("GET", V2, f"/pages/{content_id}/descendants", params={"depth": depth}, operation="confluence.content.descendants")
        return await self._page(spec, LinkCursorPagination(), self._content, limit=limit, cursor=cursor)

    async def list_versions(self, content_id: str, *, limit: int = 25, cursor: str | None = None) -> Page[ContentVersion]:
        spec = RequestSpec("GET", V2, f"/pages/{content_id}/versions", operation="confluence.content.versions")
        return await self._page(spec, LinkCursorPagination(), n.content_version, limit=limit, cursor=cursor)

    async def diff_versions(self, content_id: str, from_version: int, to_version: int) -> ContentDiff:
        """Client-side unified diff of the Markdown of two versions (REST has no diff endpoint)."""
        a = await self.get_content(content_id, version=from_version)
        b = await self.get_content(content_id, version=to_version)
        diff = "\n".join(difflib.unified_diff((a.body_markdown or "").splitlines(), (b.body_markdown or "").splitlines(),
                                              f"v{from_version}", f"v{to_version}", lineterm=""))
        return ContentDiff(content_id=content_id, from_version=from_version, to_version=to_version, unified=diff)

    async def list_labels(self, content_id: str) -> list[Label]:
        data = await self._json(V2, f"/pages/{content_id}/labels", operation="confluence.label.list")
        return [n.label(label) for label in data.get("results", [])]

    async def get_properties(self, content_id: str) -> dict[str, Any]:
        data = await self._json(V2, f"/pages/{content_id}/properties", operation="confluence.property.get")
        return {p["key"]: p.get("value") for p in data.get("results", [])}

    async def list_comments(self, content_id: str, *, kind: Literal["footer", "inline", "all"] = "all", limit: int = 25,
                            cursor: str | None = None) -> Page[ConfluenceComment]:
        if kind == "all":
            footer = await self.list_comments(content_id, kind="footer", limit=limit)
            inline = await self.list_comments(content_id, kind="inline", limit=limit)
            return Page(items=[*footer.items, *inline.items])
        path = f"/pages/{content_id}/{'footer' if kind == 'footer' else 'inline'}-comments"
        spec = RequestSpec("GET", V2, path, params={"body-format": "storage"}, operation="confluence.comment.list")
        return await self._page(spec, LinkCursorPagination(), lambda d: n.conf_comment(d, kind), limit=limit, cursor=cursor)

    async def list_attachments(self, content_id: str, *, limit: int = 25, cursor: str | None = None) -> Page[Attachment]:
        spec = RequestSpec("GET", V2, f"/pages/{content_id}/attachments", operation="confluence.attachment.list")
        return await self._page(spec, LinkCursorPagination(), n.conf_attachment, limit=limit, cursor=cursor)

    async def download_attachment(self, attachment_id: str, *, dest: Path | None = None, max_bytes: int | None = None,
                                  extract_text: bool = False) -> AttachmentContent:
        meta = await self._json(V2, f"/attachments/{attachment_id}", operation="confluence.attachment.download")
        link = meta.get("downloadLink")
        if not link or not self._wiki_base:
            raise NotFoundError(f"attachment {attachment_id} has no download link", hint="Check you can view the page.")
        size = int(meta.get("fileSize") or 0)
        if max_bytes is not None and size > max_bytes:
            from ...core.errors import error_for_status

            raise error_for_status(413, f"attachment is {size} bytes, above max_bytes={max_bytes}")
        data = await self.transport.stream_download(
            RequestSpec("GET", V2, self._wiki_base + str(link), headers={"Accept": "*/*"}, operation="confluence.attachment.download"),
            max_bytes=max_bytes)
        mime = meta.get("mediaType")
        path = None
        if dest is not None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            path = str(dest)
        text = data.decode("utf-8", errors="replace") if extract_text and (mime or "").startswith("text/") else None
        return AttachmentContent(attachment_id=attachment_id, filename=meta.get("title"), mime_type=mime, size=len(data),
                                 data=None if dest else data, path=path, text=text)

    async def get_permissions(self, content_id: str) -> Permissions:
        data = await self._json(V2, f"/pages/{content_id}/operations", operation="confluence.permission.get")
        return Permissions(operations=list(data.get("operations", [])))

    async def get_restrictions(self, content_id: str) -> Restrictions:
        data = await self._json(V1, f"/content/{content_id}/restriction/byOperation", operation="confluence.permission.restrictions.get")
        return n.restrictions(data)

    async def list_tasks(self, *, space_id: str | None = None, content_id: str | None = None,
                         status: Literal["complete", "incomplete"] | None = None, limit: int = 25,
                         cursor: str | None = None) -> Page[Task]:
        spec = RequestSpec("GET", V2, "/tasks", params={"space-id": space_id, "page-id": content_id, "status": status,
                                                        "body-format": "storage"}, operation="confluence.task.list")
        return await self._page(spec, LinkCursorPagination(), n.conf_task, limit=limit, cursor=cursor)

    async def resolve_macros(self, content_id: str) -> Content:
        """Partial: the rendered ``view`` body (macros expanded by Confluence) converted to Markdown."""
        data = await self._json(V2, f"/pages/{content_id}", params={"body-format": "view"}, operation="confluence.content.macros.resolve")
        view = ((data.get("body") or {}).get("view") or {}).get("value")
        c = self._content(data)
        c.body_markdown = storage_to_markdown(view) if view else c.body_markdown
        return c

    # ---------------------------------------------------------------- search
    async def search_cql(self, cql: str, *, limit: int = 25, cursor: str | None = None, excerpt: bool = True) -> Page[SearchHit]:
        """CQL search through v1 ``/wiki/rest/api/search``. Returns ids/excerpts only; hydrate bodies via get_content."""
        if not cql.strip():
            raise InvalidRequestError("empty CQL query", hint='Example: space = "ENG" AND type = page AND text ~ "runbook".')
        spec = RequestSpec("GET", V1, "/search", params={"cql": cql, "excerpt": "highlight" if excerpt else "none"},
                           operation="confluence.search.cql")
        return await self._page(spec, CqlPagination(), n.search_hit, limit=limit, cursor=cursor)

    # ---------------------------------------------------------------- spaces / templates
    async def list_spaces(self, *, keys: Sequence[str] = (), type: str | None = None, limit: int = 25,
                          cursor: str | None = None) -> Page[Space]:
        spec = RequestSpec("GET", V2, "/spaces", params={"keys": ",".join(keys) or None, "type": type},
                           operation="confluence.space.list")
        return await self._page(spec, LinkCursorPagination(), n.space, limit=limit, cursor=cursor)

    async def get_space(self, key_or_id: str) -> Space:
        if key_or_id.isdigit():
            return n.space(await self._json(V2, f"/spaces/{key_or_id}", operation="confluence.space.get"))
        page = await self.list_spaces(keys=[key_or_id], limit=1)
        if not page.items:
            raise NotFoundError(f"space {key_or_id!r} not found", hint="Space keys are case-sensitive; or you lack access.")
        return page.items[0]

    async def get_personal_space(self, account_id: str | None = None) -> Space:
        data = await self._json(V2, "/spaces", params={"type": "personal", "limit": 250}, operation="confluence.space.personal")
        spaces = [n.space(s) for s in data.get("results", [])]
        wanted = account_id
        for s in spaces:
            if wanted is None or (s.raw or {}).get("authorId") == wanted:
                return s
        raise NotFoundError("personal space not found")

    async def get_space_instructions(self, key_or_id: str) -> str | None:
        raise UnsupportedOperationError("space instructions are not available through the REST API",
                                        hint="They are available through the MCP backend (getConfluenceSpaceInstructions).",
                                        operation="confluence.space.instructions.get")

    async def list_space_permissions(self, key_or_id: str, *, limit: int = 50, cursor: str | None = None) -> Page[dict[str, Any]]:
        space = await self.get_space(key_or_id)
        spec = RequestSpec("GET", V2, f"/spaces/{space.id}/permissions", operation="confluence.space.permissions.list")
        return await self._page(spec, LinkCursorPagination(), dict, limit=limit, cursor=cursor)

    async def list_templates(self, *, space_key: str | None = None) -> list[Template]:
        data = await self._json(V1, "/template/page", params={"spaceKey": space_key}, operation="confluence.template.list")
        return [n.template(t) for t in data.get("results", [])]

    async def get_template(self, template_id: str) -> Template:
        return n.template(await self._json(V1, f"/template/{template_id}", operation="confluence.template.get"))


__all__ = ["ConfluenceRest", "adf_to_markdown"]
