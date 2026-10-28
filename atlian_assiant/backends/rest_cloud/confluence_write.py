"""Confluence Cloud REST writes: v2 where available, v1 for move/copy/archive/restrictions/labels (design sections 9.5, 10.1)."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ...core.errors import ConflictError, InvalidRequestError, NotFoundError, UnsupportedOperationError
from ...core.http import RequestSpec
from ...core.resolvers import SpaceResolver
from ...core.urls import ApiSurface
from ...models.common import BodyInput
from ...models.confluence import (
    Attachment,
    ConfluenceComment,
    Content,
    ContentDraft,
    ContentUpdate,
    Label,
    Restrictions,
    Space,
    Task,
)
from ...protocols.confluence import ContentType
from . import normalize as n
from .base import RestBase
from .bodies import storage_body
from .confluence import _COLLECTION, ConfluenceRest

V2 = ApiSurface.CONFLUENCE_V2
V1 = ApiSurface.CONFLUENCE_V1


def _unsupported(op: str, why: str, hint: str = "The MCP backend offers it.") -> UnsupportedOperationError:
    return UnsupportedOperationError(f"{op} is not available through the REST API: {why}", operation=op, hint=hint)


class ConfluenceWriteRest(RestBase):
    """Implements ConfluenceContentWriter, ConfluenceSpaceWriter, ConfluencePermissionWriter and ConfluenceContentDeleter."""

    def __init__(self, transport: Any, reader: ConfluenceRest) -> None:
        super().__init__(transport)
        self._reader = reader
        self._spaces = SpaceResolver(reader)

    async def _w(self, surface: ApiSurface, method: str, path: str, op: str, *, json: Any = None, params: dict[str, Any] | None = None,
                 key: str | None = None, idempotent: bool | None = None) -> Any:
        return await self.transport.json(RequestSpec(method, surface, path, params or {}, json=json, operation=op, serialize_key=key,
                                                     idempotent=(method in ("PUT", "DELETE")) if idempotent is None else idempotent))

    # ---------------------------------------------------------------- content
    async def create_content(self, draft: ContentDraft) -> Content:
        ctype = ContentType(draft.type)
        body: dict[str, Any] = {"status": draft.status, "title": draft.title, "body": storage_body(draft.body)}
        if ctype in (ContentType.PAGE, ContentType.BLOGPOST):
            body["spaceId"] = await self._spaces.to_id(draft.space)
        elif ctype is ContentType.FOLDER:
            body["spaceId"] = await self._spaces.to_id(draft.space)
            body.pop("body", None)
        if draft.parent_id:
            body["parentId"] = draft.parent_id
        data = await self._w(V2, "POST", f"/{_COLLECTION[ctype]}", "confluence.content.create", json=body, idempotent=False)
        return self._reader._content(data, ctype)

    async def update_content(self, content_id: str, update: ContentUpdate) -> Content:
        """Version-checked: the client reads the current version, sends ``n + 1`` and maps a 409 to ConflictError (DA 10.1)."""
        current = await self._reader.get_content(content_id, body_format="storage")
        current_version = current.version or 1
        if update.version is not None and update.version != current_version:
            raise ConflictError(f"Page version changed: you based this on v{update.version} but it is now v{current_version}.",
                                hint="Re-fetch with confluence_get_page and retry with version = current.", status=409, operation="confluence.content.update")
        body: dict[str, Any] = {"id": content_id, "status": update.status or current.status or "current", "title": update.title or current.title,
                                "version": {"number": current_version + 1, **({"message": update.message} if update.message else {})}}
        if update.body is not None:
            body["body"] = storage_body(update.body)
        elif current.body_storage is not None:
            body["body"] = {"representation": "storage", "value": current.body_storage}
        try:
            data = await self._w(V2, "PUT", f"/pages/{content_id}", "confluence.content.update", json=body, key=content_id, idempotent=False)
        except ConflictError as exc:
            exc.hint = "Page version changed. Re-fetch with confluence_get_page and retry with version = current + 1."
            raise
        return self._reader._content(data)

    async def move_content(self, content_id: str, *, target_parent_id: str | None = None, target_space: str | None = None, position: str = "append") -> Content:
        if not target_parent_id:
            raise InvalidRequestError("move needs target_parent_id", hint="Moving to a space root needs the space homepage id as target_parent_id.")
        await self._w(V1, "PUT", f"/content/{content_id}/move/{position}/{target_parent_id}", "confluence.content.move", key=content_id)
        return await self._reader.get_content(content_id)

    async def copy_content(self, content_id: str, *, destination_parent_id: str, title: str | None = None) -> Content:
        body: dict[str, Any] = {"copyAttachments": True, "copyLabels": True, "destination": {"type": "parent_page", "value": destination_parent_id}}
        if title:
            body["pageTitle"] = title
        data = await self._w(V1, "POST", f"/content/{content_id}/copy", "confluence.content.copy", json=body, idempotent=False)
        return await self._reader.get_content(str(data["id"]))

    async def archive_content(self, content_ids: Sequence[str]) -> None:
        await self._w(V1, "POST", "/content/archive", "confluence.content.archive", json={"pages": [{"id": i} for i in content_ids]}, idempotent=False)

    async def unarchive_content(self, content_ids: Sequence[str]) -> None:
        raise _unsupported("confluence.content.unarchive", "there is no REST endpoint")

    async def restore_version(self, content_id: str, version: int, *, message: str | None = None) -> Content:
        await self._w(V1, "POST", f"/content/{content_id}/version", "confluence.content.restore_version", key=content_id, idempotent=False,
                      json={"operationKey": "restore", "params": {"versionNumber": version, "message": message or f"Restored version {version}"}})
        return await self._reader.get_content(content_id)

    async def set_status(self, content_id: str, status: str) -> None:
        raise _unsupported("confluence.content.set_status", "content states are not exposed")

    async def add_labels(self, content_id: str, labels: Sequence[str]) -> list[Label]:
        data = await self._w(V1, "POST", f"/content/{content_id}/label", "confluence.label.add", json=[{"prefix": "global", "name": x} for x in labels],
                             idempotent=False)
        return [Label(name=x.get("name", ""), prefix=x.get("prefix"), id=str(x["id"]) if "id" in x else None) for x in data.get("results", [])]

    async def remove_label(self, content_id: str, label: str) -> None:
        await self._w(V1, "DELETE", f"/content/{content_id}/label/{label}", "confluence.label.remove")

    async def add_comment(self, content_id: str, body: BodyInput | str, *, parent_comment_id: str | None = None,
                          inline_anchor: dict[str, Any] | None = None) -> ConfluenceComment:
        payload: dict[str, Any] = {"pageId": content_id, "body": storage_body(body)}
        if parent_comment_id:
            payload["parentCommentId"] = parent_comment_id
        if inline_anchor:
            payload["inlineCommentProperties"] = inline_anchor
        kind = "inline" if inline_anchor else "footer"
        data = await self._w(V2, "POST", f"/{kind}-comments", "confluence.comment.create", json=payload, idempotent=False)
        return n.conf_comment(data, kind)

    async def update_comment(self, comment_id: str, body: BodyInput | str) -> ConfluenceComment:
        for kind in ("footer", "inline"):
            try:
                current = await self._w(V2, "GET", f"/{kind}-comments/{comment_id}", "confluence.comment.update", idempotent=True)
            except NotFoundError:
                continue
            number = ((current.get("version") or {}).get("number") or 1) + 1
            data = await self._w(V2, "PUT", f"/{kind}-comments/{comment_id}", "confluence.comment.update",
                                 json={"version": {"number": number}, "body": storage_body(body)}, idempotent=False)
            return n.conf_comment(data, kind)
        raise NotFoundError(f"comment {comment_id} not found", operation="confluence.comment.update")

    async def resolve_comment(self, comment_id: str, *, resolved: bool = True) -> None:
        current = await self._w(V2, "GET", f"/inline-comments/{comment_id}", "confluence.comment.resolve", idempotent=True)
        await self._w(V2, "PUT", f"/inline-comments/{comment_id}", "confluence.comment.resolve", idempotent=False,
                      json={"version": {"number": ((current.get("version") or {}).get("number") or 1) + 1}, "resolved": resolved,
                            "body": {"representation": "storage", "value": ((current.get("body") or {}).get("storage") or {}).get("value", "")}})

    async def upload_attachment(self, content_id: str, file: Path | bytes, *, filename: str | None = None, comment: str | None = None) -> Attachment:
        data = file if isinstance(file, bytes) else file.read_bytes()
        name = filename or (file.name if isinstance(file, Path) else "attachment")
        files: dict[str, Any] = {"file": (name, data)}
        if comment:
            files["comment"] = (None, comment)
        spec = RequestSpec("POST", V1, f"/content/{content_id}/child/attachment", files=files, headers={"X-Atlassian-Token": "nocheck"},
                           operation="confluence.attachment.upload", idempotent=False, serialize_key=content_id)
        resp = (await self.transport.send(spec)).json()
        item = (resp.get("results") or [resp])[0]
        return Attachment(id=str(item["id"]), title=item.get("title", name), media_type=(item.get("extensions") or {}).get("mediaType"),
                          file_size=(item.get("extensions") or {}).get("fileSize"), page_id=content_id, raw=item)

    async def set_property(self, content_id: str, key: str, value: Any) -> None:
        existing = await self._w(V2, "GET", f"/pages/{content_id}/properties", "confluence.property.set", params={"key": key}, idempotent=True)
        found = (existing.get("results") or [None])[0]
        if found:
            await self._w(V2, "PUT", f"/pages/{content_id}/properties/{found['id']}", "confluence.property.set", idempotent=True,
                          json={"key": key, "value": value, "version": {"number": ((found.get("version") or {}).get("number") or 1) + 1}})
        else:
            await self._w(V2, "POST", f"/pages/{content_id}/properties", "confluence.property.set", json={"key": key, "value": value}, idempotent=False)

    async def set_restrictions(self, content_id: str, restrictions: Restrictions) -> None:
        def side(op: str, users: list[Any], groups: list[str]) -> dict[str, Any]:
            return {"operation": op, "restrictions": {"user": [{"type": "known", "accountId": u.account_id} for u in users],
                                                      "group": [{"type": "group", "name": g} for g in groups]}}

        await self._w(V1, "PUT", f"/content/{content_id}/restriction", "confluence.permission.restrictions.set",
                      json=[side("read", restrictions.read_users, restrictions.read_groups), side("update", restrictions.update_users, restrictions.update_groups)])

    async def set_task_status(self, task_id: str, *, complete: bool) -> Task:
        data = await self._w(V2, "PUT", f"/tasks/{task_id}", "confluence.task.complete", json={"status": "complete" if complete else "incomplete"})
        return n.conf_task(data)

    async def watch(self, *, content_id: str | None = None, space_key: str | None = None, label: str | None = None, watch: bool = True) -> None:
        target = (f"content/{content_id}" if content_id else f"space/{space_key}" if space_key else f"label/{label}" if label else None)
        if target is None:
            raise InvalidRequestError("watch needs content_id, space_key or label", hint="Pass exactly one.")
        await self._w(V1, "POST" if watch else "DELETE", f"/user/watch/{target}", "confluence.watch.content", idempotent=not watch)

    async def star(self, *, content_id: str | None = None, space_key: str | None = None, star: bool = True) -> None:
        raise _unsupported("confluence.star.content", "favourites have no REST endpoint")

    async def add_reaction(self, content_id: str, emoji: str) -> None:
        raise _unsupported("confluence.reaction.add", "reactions have no REST endpoint")

    # ---------------------------------------------------------------- spaces / permissions
    async def create_space(self, key: str, name: str, *, description: str | None = None) -> Space:
        body: dict[str, Any] = {"key": key, "name": name}
        if description:
            body["description"] = {"plain": {"value": description, "representation": "plain"}}
        data = await self._w(V1, "POST", "/space", "confluence.space.create", json=body, idempotent=False)
        return await self._reader.get_space(data.get("key", key))

    async def set_space_instructions(self, key_or_id: str, instructions: str) -> None:
        raise _unsupported("confluence.space.instructions.set", "no REST API")

    async def _restriction_map(self, content_id: str) -> Restrictions:
        return await self._reader.get_restrictions(content_id)

    async def add_permissions(self, content_id: str, grants: Sequence[dict[str, Any]]) -> None:
        current = await self._restriction_map(content_id)
        await self.set_restrictions(content_id, _apply(current, grants, remove=False))

    async def remove_permissions(self, content_id: str, grants: Sequence[dict[str, Any]]) -> None:
        current = await self._restriction_map(content_id)
        await self.set_restrictions(content_id, _apply(current, grants, remove=True))

    async def replace_permissions(self, content_id: str, grants: Sequence[dict[str, Any]]) -> None:
        await self.set_restrictions(content_id, _apply(Restrictions(), grants, remove=False))

    async def set_public_link(self, content_id: str, *, enabled: bool) -> None:
        raise _unsupported("confluence.content.public_link.set", "public links have no REST endpoint")

    # ---------------------------------------------------------------- deleter
    async def delete_content(self, content_id: str, *, type: ContentType = ContentType.PAGE, purge: bool = False) -> None:
        await self._w(V2, "DELETE", f"/{_COLLECTION[type]}/{content_id}", "confluence.content.delete", params={"purge": str(purge).lower()})

    async def delete_comment(self, comment_id: str) -> None:
        for kind in ("footer", "inline"):
            try:
                await self._w(V2, "DELETE", f"/{kind}-comments/{comment_id}", "confluence.comment.delete")
                return
            except NotFoundError:
                continue
        raise NotFoundError(f"comment {comment_id} not found", operation="confluence.comment.delete")

    async def delete_attachment(self, attachment_id: str, *, purge: bool = False) -> None:
        await self._w(V2, "DELETE", f"/attachments/{attachment_id}", "confluence.attachment.delete", params={"purge": str(purge).lower()})


def _apply(current: Restrictions, grants: Sequence[dict[str, Any]], *, remove: bool) -> Restrictions:
    """Grants look like ``{"operation": "read"|"update", "user": accountId} | {"operation": ..., "group": name}``."""
    from ...models.common import UserRef

    sides = {"read": ([u for u in current.read_users], list(current.read_groups)), "update": ([u for u in current.update_users], list(current.update_groups))}
    for g in grants:
        op = g.get("operation")
        if op not in sides:
            raise InvalidRequestError(f"grant operation must be 'read' or 'update', got {op!r}", hint='Example: {"operation": "read", "group": "eng"}.')
        users, groups = sides[op]
        if "user" in g:
            users[:] = [u for u in users if u.account_id != g["user"]] + ([] if remove else [UserRef(account_id=g["user"])])
        if "group" in g:
            groups[:] = [x for x in groups if x != g["group"]] + ([] if remove else [g["group"]])
    return Restrictions(read_users=sides["read"][0], read_groups=sides["read"][1], update_users=sides["update"][0], update_groups=sides["update"][1])
