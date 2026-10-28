"""JiraMirror (design 15, DA 6.1): initial load, incremental via the event plane, detail on demand, nightly reconcile with an id-set diff.

The entity id in the store is the issue KEY (events carry keys). A moved issue therefore appears under its new key; the reconcile pass tombstones the old one.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..core.errors import NotFoundError
from ..events.model import ChangeAction, ChangeEvent, EntityType
from ..models.jira import Issue
from .base import MirrorStore, newer

KIND = "issue"
PROJECTION = ("summary", "status", "assignee", "reporter", "labels", "created", "updated", "description", "issuetype", "project", "priority", "parent")
ISSUE_FAMILY = {EntityType.ISSUE, EntityType.COMMENT, EntityType.WORKLOG, EntityType.ATTACHMENT, EntityType.ISSUE_LINK, EntityType.ISSUE_PROPERTY}


@dataclass
class SyncResult:
    loaded: int = 0
    skipped: int = 0                    # already current (version gate)
    tombstoned: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class ReconcileResult:
    compared: int = 0
    created: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0


class JiraMirror:
    def __init__(self, client: Any, store: MirrorStore, *, jql: str, scope: str = "default", fields: Sequence[str] = PROJECTION) -> None:
        self.client, self.store, self.jql, self.scope, self.fields = client, store, jql, scope, tuple(fields)

    # ---------------------------------------------------------------------------------------------- documents
    def document(self, issue: Issue) -> dict[str, Any]:
        src = issue.source
        return {"_scope": self.scope, "_parent": f"issue:{issue.parent_key}" if issue.parent_key else None, "_synced_at": datetime.now(UTC).isoformat(),
                "cloud_id": src.cloud_id if src else None, "site": self.client.config.site, "id": issue.id, "key": issue.key, "url": issue.url,
                "summary": issue.summary, "status": issue.status.name if issue.status else None, "status_category": issue.status.category if issue.status else None,
                "issue_type": issue.issue_type, "project": issue.project.key if issue.project else None, "priority": issue.priority,
                "assignee": issue.assignee.model_dump(mode="json", exclude={"source", "raw"}) if issue.assignee else None,
                "reporter": issue.reporter.model_dump(mode="json", exclude={"source", "raw"}) if issue.reporter else None,
                "labels": list(issue.labels), "parent_key": issue.parent_key, "created": issue.created.isoformat() if issue.created else None,
                "updated": issue.updated.isoformat() if issue.updated else None, "description_markdown": issue.description_markdown,
                "description_adf": issue.description_adf}

    @staticmethod
    def version(issue: Issue) -> str:
        return issue.updated.isoformat() if issue.updated else ""

    async def _put(self, issue: Issue) -> bool:
        version = self.version(issue)
        if not newer(version, await self.store.get_version(KIND, issue.key)):
            return False
        await self.store.upsert(KIND, issue.key, version, self.document(issue))
        return True

    # ---------------------------------------------------------------------------------------------- initial load / incremental / reconcile
    async def initial_load(self, *, max_items: int | None = None) -> SyncResult:
        res = SyncResult()
        async for issue in self.client.jira.issues.iter_search(self.jql, fields=self.fields, max_items=max_items):
            try:
                res.loaded += await self._put(issue)
                res.skipped += 0 if res.loaded else 1
            except Exception as exc:                                       # one bad document must not stop a bulk load; it is reported
                res.errors.append(f"{issue.key}: {type(exc).__name__}: {exc}")
        return res

    async def apply_event(self, event: ChangeEvent) -> str:
        """Refresh from FRESH state (never the payload). -> upserted | stale | tombstoned | ignored."""
        if event.entity_type not in ISSUE_FAMILY or not event.entity_key:
            return "ignored"
        try:
            issue = await self.client.jira.issues.get(event.entity_key)
        except NotFoundError:
            await self.store.tombstone(KIND, event.entity_key)
            return "tombstoned"
        if event.action is ChangeAction.DELETED and event.entity_type is EntityType.ISSUE:
            await self.store.tombstone(KIND, event.entity_key)
            return "tombstoned"
        return "upserted" if await self._put(issue) else "stale"

    async def sink(self, event: ChangeEvent) -> None:
        """An event-plane sink: plug the mirror in with ``plane.add_sink(mirror.sink)``."""
        await self.apply_event(event)

    async def reconcile(self) -> ReconcileResult:
        """Nightly: diff the scope's current id set against the store; tombstone what vanished, refresh what changed or is new."""
        res = ReconcileResult()
        current: dict[str, Issue] = {}
        async for issue in self.client.jira.issues.iter_search(self.jql, fields=self.fields):
            current[issue.key] = issue
        known = {i async for i in self.store.known_ids(KIND, self.scope)}
        res.compared = len(current | dict.fromkeys(known))
        for key in sorted(known - set(current)):
            await self.store.tombstone(KIND, key)
            res.deleted += 1
        for key, issue in current.items():
            existed = key in known
            if await self._put(issue):
                res.updated += existed
                res.created += not existed
            else:
                res.unchanged += 1
        return res

    # ---------------------------------------------------------------------------------------------- detail on demand (never stored by default)
    async def comments(self, key: str) -> Any:
        return await self.client.jira.issues.comments(key)

    async def worklogs(self, key: str) -> Any:
        return await self.client.jira.issues.worklogs(key)

    async def changelog(self, key: str) -> Any:
        return await self.client.jira.issues.changelog(key)

    async def attachments(self, key: str) -> Any:
        return await self.client.jira.issues.attachments(key)
