"""ConfluenceMirror (design 15, DA 6.2): CQL ids -> hydrate in batches, incremental via the event plane, RESTRICTIONS mirrored for downstream authorization, nightly
reconcile with an id-set diff and subtree checks (Confluence emits no per-child delete events)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..core.errors import NotFoundError, PermissionDeniedError
from ..events.model import ChangeAction, ChangeEvent, EntityType
from ..models.common import UserRef
from ..models.confluence import Content, Restrictions
from .base import MirrorStore, newer
from .jira import ReconcileResult, SyncResult

KIND = "page"
CONTENT_FAMILY = {EntityType.PAGE, EntityType.BLOGPOST, EntityType.COMMENT, EntityType.ATTACHMENT, EntityType.LABEL}


@dataclass
class ConfluenceReconcileResult(ReconcileResult):
    subtree_tombstoned: list[str] = field(default_factory=list)


class ConfluenceMirror:
    def __init__(self, client: Any, store: MirrorStore, *, cql: str, scope: str = "default", batch_size: int = 25, mirror_restrictions: bool = True) -> None:
        self.client, self.store, self.cql, self.scope, self.batch_size, self.mirror_restrictions = client, store, cql, scope, batch_size, mirror_restrictions

    # ---------------------------------------------------------------------------------------------- documents
    @staticmethod
    def _restrictions(r: Restrictions) -> dict[str, Any]:
        def users(us: list[UserRef]) -> list[str | None]:
            return [u.account_id or u.display_name for u in us]

        return {"restricted": r.restricted, "read_users": users(r.read_users), "read_groups": list(r.read_groups), "update_users": users(r.update_users),
                "update_groups": list(r.update_groups)}

    async def hydrate(self, content_id: str) -> tuple[Content, dict[str, Any] | None]:
        page = await self.client.confluence.content.get(content_id)
        restrictions = None
        if self.mirror_restrictions:
            try:
                restrictions = self._restrictions(await self.client.confluence.permissions.restrictions(content_id))
            except PermissionDeniedError:
                restrictions = {"restricted": None, "unknown": True}              # unknown must never read as "unrestricted"
        return page, restrictions

    def document(self, page: Content, restrictions: dict[str, Any] | None) -> dict[str, Any]:
        return {"_scope": self.scope, "_parent": f"page:{page.parent_id}" if page.parent_id else None, "_synced_at": datetime.now(UTC).isoformat(),
                "site": self.client.config.site, "id": page.id, "type": page.type, "title": page.title, "status": page.status, "space_id": page.space_id, "space_key": page.space_key,
                "parent_id": page.parent_id, "version": page.version, "lastmodified": page.updated.isoformat() if page.updated else None, "url": page.url,
                "body_markdown": page.body_markdown, "body_storage": page.body_storage, "restrictions": restrictions}

    @staticmethod
    def version(page: Content) -> str:
        return "" if page.version is None else str(page.version)

    async def _put(self, page: Content, restrictions: dict[str, Any] | None) -> bool:
        version = self.version(page)
        if not newer(version, await self.store.get_version(KIND, page.id)):
            return False
        await self.store.upsert(KIND, page.id, version, self.document(page, restrictions))
        return True

    # ---------------------------------------------------------------------------------------------- initial load / incremental / reconcile
    async def initial_load(self, *, max_items: int | None = None) -> SyncResult:
        res = SyncResult()
        batch: list[str] = []

        async def flush() -> None:
            for cid in batch:
                try:
                    page, restrictions = await self.hydrate(cid)
                    if await self._put(page, restrictions):
                        res.loaded += 1
                    else:
                        res.skipped += 1
                except NotFoundError:
                    res.skipped += 1                                            # deleted between the search and the read
                except Exception as exc:
                    res.errors.append(f"{cid}: {type(exc).__name__}: {exc}")
            batch.clear()

        async for hit in self.client.confluence.search.iter_cql(self.cql, max_items=max_items):
            if hit.content_id:
                batch.append(hit.content_id)
            if len(batch) >= self.batch_size:
                await flush()
        await flush()
        return res

    async def _tombstone_tree(self, content_id: str) -> list[str]:
        gone = await self.store.descendants(KIND, content_id)
        for cid in (content_id, *gone):
            await self.store.tombstone(KIND, cid)
        return gone

    async def apply_event(self, event: ChangeEvent) -> str:
        if event.entity_type not in CONTENT_FAMILY or not event.entity_id:
            return "ignored"
        if event.entity_type in (EntityType.COMMENT, EntityType.ATTACHMENT, EntityType.LABEL):
            return "ignored"                                                   # detail is read on demand; the page's own version covers body changes
        if event.action in (ChangeAction.DELETED, ChangeAction.SOFT_DELETED):
            await self._tombstone_tree(event.entity_id)
            return "tombstoned"
        try:
            page, restrictions = await self.hydrate(event.entity_id)
        except NotFoundError:
            await self._tombstone_tree(event.entity_id)
            return "tombstoned"
        return "upserted" if await self._put(page, restrictions) else "stale"

    async def sink(self, event: ChangeEvent) -> None:
        await self.apply_event(event)

    async def reconcile(self) -> ConfluenceReconcileResult:
        res = ConfluenceReconcileResult()
        current: dict[str, Any] = {}
        async for hit in self.client.confluence.search.iter_cql(self.cql):
            if hit.content_id:
                current[hit.content_id] = hit
        known = {i async for i in self.store.known_ids(KIND, self.scope)}
        res.compared = len(set(current) | known)
        for cid in sorted(known - set(current)):
            res.subtree_tombstoned += await self._tombstone_tree(cid)           # a deleted parent takes its known children with it
            res.deleted += 1
        for cid in sorted(current):
            existed = cid in known
            try:
                page, restrictions = await self.hydrate(cid)
            except NotFoundError:
                continue
            if await self._put(page, restrictions):
                res.updated += existed
                res.created += not existed
            else:
                res.unchanged += 1
        return res

    async def comments(self, content_id: str) -> Any:
        return await self.client.confluence.comments.list(content_id)

    async def attachments(self, content_id: str) -> Any:
        return await self.client.confluence.attachments.list(content_id)


