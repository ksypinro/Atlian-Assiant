"""Confluence Data Center REST writes: v1 content APIs (design 10.2). Operations without a Data Center endpoint raise UnsupportedOperationError."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ...core.errors import ConflictError, InvalidRequestError, NotFoundError, UnsupportedOperationError
from ...core.urls import ApiSurface
from ...formats.storage import markdown_to_storage
from ...models.common import BodyInput
from ...models.confluence import ConfluenceComment, Content, ContentDraft, ContentUpdate
from ...protocols.confluence import ContentType
from ..rest_cloud.bodies import body_text
from ..rest_cloud.confluence_write import ConfluenceWriteRest
from .confluence import ConfluenceDcRest, dc_content

V1 = ApiSurface.CONFLUENCE_V1


def _storage(body: BodyInput | str) -> dict[str, str]:
    b = body if isinstance(body, BodyInput) else BodyInput(format="markdown", value=body)
    if b.format == "storage":
        return {"representation": "storage", "value": str(b.value)}
    if b.format == "plain":
        import html
        return {"representation": "storage", "value": f"<p>{html.escape(str(b.value))}</p>"}
    return {"representation": "storage", "value": markdown_to_storage(body_text(b))}


def _no(op: str, why: str) -> UnsupportedOperationError:
    return UnsupportedOperationError(f"{op} is not available on Confluence Data Center: {why}", operation=op, hint="Use Confluence Cloud, or do it in the UI.")


class ConfluenceDcWrite(ConfluenceWriteRest):
    async def _key(self, space: str) -> str:
        return (await self._reader.get_space(space)).key

    @property
    def _dc(self) -> ConfluenceDcRest:
        assert isinstance(self._reader, ConfluenceDcRest)
        return self._reader

    async def create_content(self, draft: ContentDraft, *, storage_body: str | None = None) -> Content:
        ctype = ContentType(draft.type)
        body: dict[str, Any] = {"type": {ContentType.PAGE: "page", ContentType.BLOGPOST: "blogpost"}.get(ctype, "page"), "title": draft.title, "status": draft.status,
                                "space": {"key": await self._key(draft.space)},
                                "body": {"storage": {"representation": "storage", "value": storage_body} if storage_body is not None else _storage(draft.body)}}
        if draft.parent_id:
            body["ancestors"] = [{"id": draft.parent_id}]
        data = await self._w(V1, "POST", "/content", "confluence.content.create", json=body, idempotent=False)
        return await self._reader.get_content(str(data["id"]))

    async def update_content(self, content_id: str, update: ContentUpdate) -> Content:
        current = await self._reader.get_content(content_id)
        current_version = current.version or 1
        if update.version is not None and update.version != current_version:
            raise ConflictError(f"Page version changed: you based this on v{update.version} but it is now v{current_version}.",
                                hint="Re-fetch with confluence_get_page and retry with version = current.", status=409, operation="confluence.content.update")
        body: dict[str, Any] = {"id": content_id, "type": current.type, "title": update.title or current.title, "status": update.status or current.status or "current",
                                "version": {"number": current_version + 1, **({"message": update.message} if update.message else {})},
                                "body": {"storage": _storage(update.body) if update.body is not None else {"representation": "storage", "value": current.body_storage or ""}}}
        data = await self._w(V1, "PUT", f"/content/{content_id}", "confluence.content.update", json=body, key=content_id, idempotent=False)
        return dc_content(data, self._dc._base)

    async def copy_content(self, content_id: str, *, destination_parent_id: str, title: str | None = None) -> Content:
        """Client-side copy (read, then create under the destination): Data Center has no copy endpoint everywhere. Attachments are not copied."""
        src = await self._reader.get_content(content_id)
        parent = await self._reader.get_content(destination_parent_id)
        draft = ContentDraft(type="page", title=title or f"Copy of {src.title}", space=parent.space_key or "", parent_id=destination_parent_id)
        return await self.create_content(draft, storage_body=src.body_storage or "")

    async def archive_content(self, content_ids: Sequence[str]) -> None:
        raise _no("confluence.content.archive", "archiving is not exposed through the REST API")

    async def add_comment(self, content_id: str, body: BodyInput | str, *, parent_comment_id: str | None = None, inline_anchor: dict[str, Any] | None = None) -> ConfluenceComment:
        if inline_anchor:
            raise _no("confluence.comment.create", "inline comments cannot be created through the REST API")
        payload: dict[str, Any] = {"type": "comment", "container": {"id": content_id, "type": "page"}, "body": {"storage": _storage(body)}}
        if parent_comment_id:
            payload["ancestors"] = [{"id": parent_comment_id}]
        data = await self._w(V1, "POST", "/content", "confluence.comment.create", json=payload, idempotent=False)
        page = await self._reader.list_comments(content_id, limit=200)
        found = next((c for c in page.items if c.id == str(data["id"])), None)
        return found or ConfluenceComment(id=str(data["id"]), kind="footer", raw=data)

    async def update_comment(self, comment_id: str, body: BodyInput | str) -> ConfluenceComment:
        try:
            current = await self._w(V1, "GET", f"/content/{comment_id}", "confluence.comment.update", params={"expand": "version,container"}, idempotent=True)
        except NotFoundError as exc:
            raise NotFoundError(f"comment {comment_id} not found", operation="confluence.comment.update") from exc
        number = ((current.get("version") or {}).get("number") or 1) + 1
        data = await self._w(V1, "PUT", f"/content/{comment_id}", "confluence.comment.update", idempotent=False,
                             json={"id": comment_id, "type": "comment", "version": {"number": number}, "body": {"storage": _storage(body)}})
        return ConfluenceComment(id=str(data["id"]), kind="footer", body_markdown=body_text(body), raw=data)

    async def resolve_comment(self, comment_id: str, *, resolved: bool = True) -> None:
        raise _no("confluence.comment.resolve", "resolving comments has no REST endpoint")

    async def set_property(self, content_id: str, key: str, value: Any) -> None:
        existing = await self._w(V1, "GET", f"/content/{content_id}/property/{key}", "confluence.property.set", idempotent=True) if await self._has_property(content_id, key) else None
        if existing:
            await self._w(V1, "PUT", f"/content/{content_id}/property/{key}", "confluence.property.set", idempotent=True,
                          json={"key": key, "value": value, "version": {"number": ((existing.get("version") or {}).get("number") or 1) + 1}})
        else:
            await self._w(V1, "POST", f"/content/{content_id}/property", "confluence.property.set", json={"key": key, "value": value}, idempotent=False)

    async def _has_property(self, content_id: str, key: str) -> bool:
        return key in await self._reader.get_properties(content_id)

    async def set_task_status(self, task_id: str, *, complete: bool) -> Any:
        raise _no("confluence.task.complete", "there is no tasks API")

    async def delete_content(self, content_id: str, *, type: ContentType = ContentType.PAGE, purge: bool = False) -> None:
        await self._w(V1, "DELETE", f"/content/{content_id}", "confluence.content.delete", params={"status": "trashed"} if purge else {})

    async def delete_comment(self, comment_id: str) -> None:
        await self._w(V1, "DELETE", f"/content/{comment_id}", "confluence.comment.delete")

    async def delete_attachment(self, attachment_id: str, *, purge: bool = False) -> None:
        await self._w(V1, "DELETE", f"/content/{attachment_id}", "confluence.attachment.delete")

    async def create_space(self, key: str, name: str, *, description: str | None = None) -> Any:
        return await super().create_space(key, name, description=description)

    async def _restriction_map(self, content_id: str) -> Any:
        return await self._reader.get_restrictions(content_id)


__all__ = ["ConfluenceDcWrite", "InvalidRequestError"]
