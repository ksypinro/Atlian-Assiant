"""Confluence Data Center REST reads: v1 content APIs only (design 10.2)."""
from __future__ import annotations

import difflib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

from ...core.errors import InvalidRequestError, NotFoundError, UnsupportedOperationError, error_for_status
from ...core.http import RequestSpec
from ...core.pagination import Cursor
from ...core.urls import ApiSurface
from ...formats.storage import storage_to_markdown
from ...formats.wiki import wiki_to_markdown
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
from ..rest_cloud import normalize as n
from ..rest_cloud.confluence import ConfluenceRest

V1 = ApiSurface.CONFLUENCE_V1
_TYPE = {ContentType.PAGE: "page", ContentType.BLOGPOST: "blogpost", ContentType.WHITEBOARD: "whiteboard", ContentType.DATABASE: "database",
         ContentType.FOLDER: "folder", ContentType.SMART_LINK: "embed", ContentType.CUSTOM: "custom"}
EXPAND = "body.storage,version,space,ancestors,history"


def dc_content(d: dict[str, Any], base: str | None = None) -> Content:
    storage = ((d.get("body") or {}).get("storage") or {}).get("value")
    view = ((d.get("body") or {}).get("view") or {}).get("value")
    ver = d.get("version") or {}
    ancestors = d.get("ancestors") or []
    space = d.get("space") or {}
    webui = (d.get("_links") or {}).get("webui")
    created = ((d.get("history") or {}).get("createdDate"))
    return Content(id=str(d["id"]), type=d.get("type", "page"), title=d.get("title"), status=d.get("status"),
                   space_id=str(space["id"]) if space.get("id") else None, space_key=space.get("key"),
                   parent_id=str(ancestors[-1]["id"]) if ancestors else None, version=ver.get("number"),
                   author_id=((ver.get("by") or {}).get("userKey") or (ver.get("by") or {}).get("username")), created=n.dt(created),
                   updated=n.dt(ver.get("when")), url=(base + webui) if base and webui else webui,
                   body_markdown=storage_to_markdown(storage) if storage is not None else (storage_to_markdown(view) if view else None),
                   body_storage=storage, raw=d)


class ConfluenceDcRest(ConfluenceRest):
    """Implements ConfluenceContentReader, ConfluenceSearcher, ConfluenceSpaceReader and ConfluenceTemplateReader against ``{base}/rest/api``."""

    @property
    def _base(self) -> str | None:
        return (self.transport.urls.dc_confluence_base or None)

    def _c(self, d: dict[str, Any]) -> Content:
        return dc_content(d, self._base)

    async def _v1_page(self, path: str, mapper: Callable[[dict[str, Any]], Any], *, params: dict[str, Any] | None = None, limit: int, cursor: str | None, op: str) -> Page[Any]:
        start = int(Cursor.decode(cursor).state["start"]) if cursor else 0
        data = await self._json(V1, path, params={**(params or {}), "start": start, "limit": limit}, operation=op)
        results = [mapper(r) for r in data.get("results", [])]
        nxt = start + len(results) if (data.get("_links") or {}).get("next") and results else None
        return Page(items=results, next_cursor=Cursor(self.kind, "dc_v1", {"start": nxt}).encode() if nxt is not None else None, total=data.get("totalSize"))

    # ---------------------------------------------------------------- content
    async def get_content(self, content_id: str, *, type: ContentType = ContentType.PAGE, body_format: BodyFormat = "markdown", version: int | None = None) -> Content:
        params: dict[str, Any] = {"expand": EXPAND if body_format != "none" else "version,space,ancestors,history"}
        if version is not None:
            params.update({"status": "historical", "version": version})
        return self._c(await self._json(V1, f"/content/{content_id}", params=params, operation="confluence.content.get"))

    async def _space_key(self, space_id: str | None) -> str | None:
        if space_id is None or not space_id.isdigit():
            return space_id
        for s in (await self.list_spaces(limit=500)).items:
            if s.id == space_id:
                return s.key
        raise NotFoundError(f"space {space_id} not found", hint="Pass the space key or a numeric id you can see.")

    async def list_content(self, *, space_id: str | None = None, type: ContentType = ContentType.PAGE, status: str = "current", title: str | None = None,
                           limit: int = 25, cursor: str | None = None) -> Page[Content]:
        params = {"type": _TYPE[type], "spaceKey": await self._space_key(space_id), "status": status, "title": title, "expand": "version,space,history"}
        return await self._v1_page("/content", self._c, params=params, limit=limit, cursor=cursor, op="confluence.content.list")

    async def list_children(self, content_id: str, *, type: ContentType = ContentType.PAGE, limit: int = 25, cursor: str | None = None) -> Page[Content]:
        return await self._v1_page(f"/content/{content_id}/child/{_TYPE[type]}", self._c, params={"expand": "version,space"}, limit=limit, cursor=cursor,
                                   op="confluence.content.children")

    async def list_ancestors(self, content_id: str, *, type: ContentType = ContentType.PAGE) -> list[Content]:
        data = await self._json(V1, f"/content/{content_id}", params={"expand": "ancestors"}, operation="confluence.content.ancestors")
        return [self._c(a) for a in data.get("ancestors", [])]

    async def list_descendants(self, content_id: str, *, depth: int | None = None, limit: int = 50, cursor: str | None = None) -> Page[Content]:
        page = await self._v1_page(f"/content/{content_id}/descendant/page", self._c, params={"expand": "version,space"}, limit=limit, cursor=cursor,
                                   op="confluence.content.descendants")
        return page

    async def list_versions(self, content_id: str, *, limit: int = 25, cursor: str | None = None) -> Page[ContentVersion]:
        def version(d: dict[str, Any]) -> ContentVersion:
            return ContentVersion(number=int(d["number"]), message=d.get("message"), author_id=(d.get("by") or {}).get("userKey") or (d.get("by") or {}).get("username"),
                                  created=n.dt(d.get("when")), minor_edit=d.get("minorEdit"), raw=d)
        return await self._v1_page(f"/content/{content_id}/version", version, limit=limit, cursor=cursor, op="confluence.content.versions")

    async def diff_versions(self, content_id: str, from_version: int, to_version: int) -> ContentDiff:
        a = await self.get_content(content_id, version=from_version)
        b = await self.get_content(content_id, version=to_version)
        diff = "\n".join(difflib.unified_diff((a.body_markdown or "").splitlines(), (b.body_markdown or "").splitlines(), f"v{from_version}", f"v{to_version}", lineterm=""))
        return ContentDiff(content_id=content_id, from_version=from_version, to_version=to_version, unified=diff)

    async def list_labels(self, content_id: str) -> list[Label]:
        data = await self._json(V1, f"/content/{content_id}/label", operation="confluence.label.list")
        return [n.label(label) for label in data.get("results", [])]

    async def get_properties(self, content_id: str) -> dict[str, Any]:
        data = await self._json(V1, f"/content/{content_id}/property", operation="confluence.property.get")
        return {p["key"]: p.get("value") for p in data.get("results", [])}

    async def list_comments(self, content_id: str, *, kind: Literal["footer", "inline", "all"] = "all", limit: int = 25, cursor: str | None = None) -> Page[ConfluenceComment]:
        def comment(d: dict[str, Any]) -> ConfluenceComment:
            location = ((d.get("extensions") or {}).get("location")) or "footer"
            storage = ((d.get("body") or {}).get("storage") or {}).get("value")
            ver = d.get("version") or {}
            return ConfluenceComment(id=str(d["id"]), kind=location, body_markdown=storage_to_markdown(storage) if storage else None,
                                     author_id=(ver.get("by") or {}).get("userKey") or (ver.get("by") or {}).get("username"), created=n.dt(ver.get("when")), raw=d)
        page = await self._v1_page(f"/content/{content_id}/child/comment", comment, params={"expand": "body.storage,version,extensions.inlineProperties"},
                                   limit=limit, cursor=cursor, op="confluence.comment.list")
        if kind != "all":
            page = Page(items=[c for c in page.items if c.kind == kind], next_cursor=page.next_cursor, total=page.total)
        return page

    async def list_attachments(self, content_id: str, *, limit: int = 25, cursor: str | None = None) -> Page[Attachment]:
        def attachment(d: dict[str, Any]) -> Attachment:
            ext = d.get("extensions") or {}
            return Attachment(id=str(d["id"]), title=d.get("title", ""), media_type=ext.get("mediaType"), file_size=ext.get("fileSize"), page_id=content_id,
                              download_url=(d.get("_links") or {}).get("download"), version=(d.get("version") or {}).get("number"), raw=d)
        return await self._v1_page(f"/content/{content_id}/child/attachment", attachment, params={"expand": "version"}, limit=limit, cursor=cursor,
                                   op="confluence.attachment.list")

    async def download_attachment(self, attachment_id: str, *, dest: Path | None = None, max_bytes: int | None = None, extract_text: bool = False) -> AttachmentContent:
        meta = await self._json(V1, f"/content/{attachment_id}", params={"expand": "version"}, operation="confluence.attachment.download")
        link = (meta.get("_links") or {}).get("download")
        if not link or not self._base:
            raise NotFoundError(f"attachment {attachment_id} has no download link", hint="Check you can view the page.")
        ext = meta.get("extensions") or {}
        size = int(ext.get("fileSize") or 0)
        if max_bytes is not None and size > max_bytes:
            raise error_for_status(413, f"attachment is {size} bytes, above max_bytes={max_bytes}", operation="confluence.attachment.download")
        data = await self.transport.stream_download(RequestSpec("GET", V1, self._base + str(link), headers={"Accept": "*/*"}, operation="confluence.attachment.download"),
                                                    max_bytes=max_bytes)
        mime = ext.get("mediaType")
        path = None
        if dest is not None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            path = str(dest)
        text = data.decode("utf-8", errors="replace") if extract_text and (mime or "").startswith("text/") else None
        return AttachmentContent(attachment_id=attachment_id, filename=meta.get("title"), mime_type=mime, size=len(data), data=None if dest else data, path=path, text=text)

    async def get_permissions(self, content_id: str) -> Permissions:
        data = await self._json(V1, f"/content/{content_id}/restriction/byOperation", operation="confluence.permission.get")
        return Permissions(operations=[{"operation": op, "restricted": bool(((v.get("restrictions") or {}).get("user") or {}).get("results")
                                                                          or ((v.get("restrictions") or {}).get("group") or {}).get("results"))}
                                       for op, v in data.items() if isinstance(v, dict)])

    async def get_restrictions(self, content_id: str) -> Restrictions:
        data = await self._json(V1, f"/content/{content_id}/restriction/byOperation", operation="confluence.permission.restrictions.get")
        return n.restrictions(data)

    async def list_tasks(self, *, space_id: str | None = None, content_id: str | None = None, status: Literal["complete", "incomplete"] | None = None,
                         limit: int = 25, cursor: str | None = None) -> Page[Task]:
        raise UnsupportedOperationError("Confluence Data Center has no tasks API", operation="confluence.task.list", hint="Read the page body and parse its task list.")

    async def resolve_macros(self, content_id: str) -> Content:
        """Partial: the rendered ``body.view`` (macros expanded by Confluence) converted to Markdown."""
        data = await self._json(V1, f"/content/{content_id}", params={"expand": "body.view,version,space,ancestors,history"}, operation="confluence.content.macros.resolve")
        return self._c(data)

    # ---------------------------------------------------------------- search
    async def search_cql(self, cql: str, *, limit: int = 25, cursor: str | None = None, excerpt: bool = True) -> Page[SearchHit]:
        if not cql.strip():
            raise InvalidRequestError("empty CQL query", hint='Example: space = "ENG" AND type = page AND text ~ "runbook".')
        return await self._v1_page("/search", n.search_hit, params={"cql": cql, "excerpt": "highlight" if excerpt else "none"}, limit=limit, cursor=cursor, op="confluence.search.cql")

    # ---------------------------------------------------------------- spaces / templates
    async def list_spaces(self, *, keys: Sequence[str] = (), type: str | None = None, limit: int = 25, cursor: str | None = None) -> Page[Space]:
        return await self._v1_page("/space", n.space, params={"spaceKey": list(keys) or None, "type": type}, limit=limit, cursor=cursor, op="confluence.space.list")

    async def get_space(self, key_or_id: str) -> Space:
        if key_or_id.isdigit():
            for s in (await self.list_spaces(limit=500)).items:
                if s.id == key_or_id:
                    return s
            raise NotFoundError(f"space {key_or_id!r} not found", hint="Use the space key.")
        return n.space(await self._json(V1, f"/space/{key_or_id}", operation="confluence.space.get"))

    async def get_personal_space(self, account_id: str | None = None) -> Space:
        if account_id:
            return n.space(await self._json(V1, f"/space/~{account_id}", operation="confluence.space.personal"))
        page = await self.list_spaces(type="personal", limit=1)
        if not page.items:
            raise NotFoundError("personal space not found")
        return page.items[0]

    async def get_space_instructions(self, key_or_id: str) -> str | None:
        raise UnsupportedOperationError("space instructions are not available on Data Center", operation="confluence.space.instructions.get", hint="Use the MCP backend on Cloud.")

    async def list_space_permissions(self, key_or_id: str, *, limit: int = 50, cursor: str | None = None) -> Page[dict[str, Any]]:
        data = await self._json(V1, f"/space/{key_or_id}", params={"expand": "permissions"}, operation="confluence.space.permissions.list")
        perms = [dict(p) for p in data.get("permissions", [])]
        start = int(Cursor.decode(cursor).state["i"]) if cursor else 0
        chunk = perms[start:start + limit]
        nxt = start + limit if start + limit < len(perms) else None
        return Page(items=chunk, next_cursor=Cursor(self.kind, "dc_list", {"i": nxt}).encode() if nxt is not None else None, total=len(perms))

    async def list_templates(self, *, space_key: str | None = None) -> list[Template]:
        data = await self._json(V1, "/template/page", params={"spaceKey": space_key}, operation="confluence.template.list")
        return [n.template(t) for t in data.get("results", [])]

    async def get_template(self, template_id: str) -> Template:
        return n.template(await self._json(V1, f"/template/{template_id}", operation="confluence.template.get"))


__all__ = ["ConfluenceDcRest", "dc_content", "wiki_to_markdown"]
