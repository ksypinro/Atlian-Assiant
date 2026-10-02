"""Jira Platform REST v3 writes: issues, deletes, versions and admin (design sections 9.2, 10.1)."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

from ...core.errors import InvalidRequestError, NotFoundError, UnsupportedOperationError
from ...core.http import RequestSpec
from ...core.resolvers import FieldResolver, UserResolver
from ...core.urls import ApiSurface
from ...models.common import BodyInput
from ...models.jira import (
    Attachment,
    Comment,
    CreatedIssue,
    IssueDraft,
    IssueUpdate,
    Project,
    RemoteLink,
    Screen,
    Version,
    VersionChange,
    Workflow,
    Worklog,
)
from . import normalize as n
from .base import RestBase
from .bodies import adf_body
from .jira import JiraRest

J = ApiSurface.JIRA_PLATFORM


class JiraWriteRest(RestBase):
    """Implements JiraIssueWriter, JiraIssueDeleter, JiraProjectWriter and JiraAdmin."""

    def __init__(self, transport: Any, reader: JiraRest) -> None:
        super().__init__(transport)
        self._reader = reader
        self._fields = FieldResolver(reader)
        self._users = UserResolver(reader)

    async def _write(self, method: str, path: str, *, op: str, key: str | None = None, json: Any = None, params: dict[str, Any] | None = None,
                     headers: dict[str, str] | None = None, idempotent: bool | None = None) -> Any:
        spec = RequestSpec(method, J, path, params or {}, json=json, headers=headers or {}, operation=op,
                           idempotent=(method in ("PUT", "DELETE")) if idempotent is None else idempotent, serialize_key=key)
        return await self.transport.json(spec)

    # ---------------------------------------------------------------- issue writer
    async def _fields_payload(self, draft_fields: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, value in draft_fields.items():
            out[await self._fields.resolve(name)] = value
        return out

    async def _issue_payload(self, draft: IssueDraft) -> dict[str, Any]:
        fields: dict[str, Any] = {"project": {"key": draft.project}, "issuetype": {"id": draft.issue_type} if draft.issue_type.isdigit() else {"name": draft.issue_type},
                                  "summary": draft.summary}
        if draft.description:
            fields["description"] = adf_body(draft.description)
        if draft.assignee:
            fields["assignee"] = {"accountId": await self._users.resolve(draft.assignee)}
        if draft.labels:
            fields["labels"] = list(draft.labels)
        if draft.parent:
            fields["parent"] = {"key": draft.parent}
        fields.update(await self._fields_payload(draft.fields))
        return {"fields": fields}

    async def create_issue(self, draft: IssueDraft) -> CreatedIssue:
        data = await self._write("POST", "/issue", op="jira.issue.create", json=await self._issue_payload(draft))
        return CreatedIssue(id=str(data["id"]), key=data["key"], url=f"{self.site_url}/browse/{data['key']}" if self.site_url else None, raw=data)

    async def bulk_create_issues(self, drafts: Sequence[IssueDraft]) -> list[CreatedIssue]:
        if not 1 <= len(drafts) <= 50:
            raise InvalidRequestError("bulk create accepts 1 to 50 issues per call", hint="Split the list into chunks of at most 50.")
        payload = {"issueUpdates": [await self._issue_payload(d) for d in drafts]}
        data = await self._write("POST", "/issue/bulk", op="jira.issue.bulk_create", json=payload)
        errors = data.get("errors") or []
        if errors:
            raise InvalidRequestError("some issues could not be created", hint="See details; the successfully created issues are in details['created'].",
                                      details={"errors": errors, "created": data.get("issues", [])})
        return [CreatedIssue(id=str(i["id"]), key=i["key"], raw=i) for i in data.get("issues", [])]

    async def edit_issue(self, key: str, update: IssueUpdate, *, notify_users: bool = True) -> None:
        fields: dict[str, Any] = {}
        if update.summary is not None:
            fields["summary"] = update.summary
        if update.description is not None:
            fields["description"] = adf_body(update.description)
        fields.update(await self._fields_payload(update.fields))
        body: dict[str, Any] = {"fields": fields}
        ops_list = [{"add": x} for x in update.labels_add] + [{"remove": x} for x in update.labels_remove]
        if ops_list:
            body["update"] = {"labels": ops_list}
        if not fields and not ops_list:
            raise InvalidRequestError("nothing to update", hint="Set at least one of summary, description, labels or fields.")
        await self._write("PUT", f"/issue/{key}", op="jira.issue.edit", key=key.upper(), json=body, params={"notifyUsers": str(notify_users).lower()})

    async def assign_issue(self, key: str, assignee: str | None) -> None:
        account = await self._users.resolve(assignee) if assignee else None
        await self._write("PUT", f"/issue/{key}/assignee", op="jira.issue.assign", key=key.upper(), json={"accountId": account})

    async def transition_issue(self, key: str, transition: str, *, fields: dict[str, Any] | None = None, comment: BodyInput | str | None = None) -> None:
        options = await self._reader.list_transitions(key)
        match = [t for t in options if transition in (t.id, t.name) or t.name.lower() == transition.lower()]
        if not match:
            raise NotFoundError(f"transition {transition!r} is not available for {key}", hint="Available: " + ", ".join(t.name for t in options),
                                operation="jira.issue.transition")
        body: dict[str, Any] = {"transition": {"id": match[0].id}}
        if fields:
            body["fields"] = await self._fields_payload(fields)
        if comment:
            body["update"] = {"comment": [{"add": {"body": adf_body(comment)}}]}
        await self._write("POST", f"/issue/{key}/transitions", op="jira.issue.transition", key=key.upper(), json=body, idempotent=False)

    async def add_comment(self, key: str, body: BodyInput | str, *, visibility: dict[str, str] | None = None) -> Comment:
        payload: dict[str, Any] = {"body": adf_body(body)}
        if visibility:
            payload["visibility"] = visibility
        return n.comment(await self._write("POST", f"/issue/{key}/comment", op="jira.issue.comment.add", key=key.upper(), json=payload, idempotent=False))

    async def edit_comment(self, key: str, comment_id: str, body: BodyInput | str) -> Comment:
        data = await self._write("PUT", f"/issue/{key}/comment/{comment_id}", op="jira.issue.comment.edit", key=key.upper(), json={"body": adf_body(body)})
        return n.comment(data)

    async def add_worklog(self, key: str, time_spent: str, *, started: str | None = None, comment: BodyInput | str | None = None) -> Worklog:
        payload: dict[str, Any] = {"timeSpent": time_spent}
        if started:
            payload["started"] = started
        if comment:
            payload["comment"] = adf_body(comment)
        return n.worklog(await self._write("POST", f"/issue/{key}/worklog", op="jira.issue.worklog.add", key=key.upper(), json=payload, idempotent=False))

    async def create_link(self, link_type: str, inward_key: str, outward_key: str) -> None:
        await self._write("POST", "/issueLink", op="jira.issue.link.create", key=inward_key.upper(),
                          json={"type": {"name": link_type}, "inwardIssue": {"key": inward_key}, "outwardIssue": {"key": outward_key}}, idempotent=False)

    async def create_remote_link(self, key: str, url: str, title: str) -> RemoteLink:
        data = await self._write("POST", f"/issue/{key}/remotelink", op="jira.issue.remote_link.create", key=key.upper(),
                                 json={"object": {"url": url, "title": title}}, idempotent=False)
        return RemoteLink(id=str(data.get("id", "")) or None, url=url, title=title, raw=data)

    async def upload_attachment(self, key: str, file: Path | bytes, *, filename: str | None = None) -> list[Attachment]:
        data = file if isinstance(file, bytes) else file.read_bytes()
        name = filename or (file.name if isinstance(file, Path) else "attachment")
        spec = RequestSpec("POST", J, f"/issue/{key}/attachments", files={"file": (name, data)}, headers={"X-Atlassian-Token": "no-check"},
                           operation="jira.issue.attachment.upload", idempotent=False, serialize_key=key.upper())
        resp = await self.transport.send(spec)
        return [n.attachment(a) for a in resp.json()]

    async def watch_issue(self, key: str, *, watch: bool = True, account_id: str | None = None) -> None:
        if watch:
            await self._write("POST", f"/issue/{key}/watchers", op="jira.issue.watch", key=key.upper(), json=account_id or (await self._reader.myself()).account_id)
        else:
            await self._write("DELETE", f"/issue/{key}/watchers", op="jira.issue.watch", key=key.upper(),
                              params={"accountId": account_id or (await self._reader.myself()).account_id})

    async def set_property(self, key: str, property_key: str, value: Any) -> None:
        await self._write("PUT", f"/issue/{key}/properties/{property_key}", op="jira.issue.property.set", key=key.upper(), json=value)

    async def convert_hierarchy(self, key: str, *, new_issue_type: str, new_parent: str | None = None) -> None:
        raise UnsupportedOperationError("changing an issue's hierarchy level is not available through the REST API", operation="jira.issue.hierarchy.convert",
                                        hint="The MCP backend offers convertJiraIssueHierarchy.")

    # ---------------------------------------------------------------- deleter
    async def delete_issue(self, key: str, *, delete_subtasks: bool = False) -> None:
        await self._write("DELETE", f"/issue/{key}", op="jira.issue.delete", key=key.upper(), params={"deleteSubtasks": str(delete_subtasks).lower()})

    async def delete_comment(self, key: str, comment_id: str) -> None:
        await self._write("DELETE", f"/issue/{key}/comment/{comment_id}", op="jira.issue.comment.delete", key=key.upper())

    async def delete_attachment(self, attachment_id: str) -> None:
        await self._write("DELETE", f"/attachment/{attachment_id}", op="jira.issue.attachment.delete")

    # ---------------------------------------------------------------- project writer
    async def manage_version(self, project: str, change: VersionChange) -> Version:
        proj = await self._reader.get_project(project)
        body: dict[str, Any] = {k: v for k, v in {"name": change.name, "releaseDate": change.release_date}.items() if v}
        action = change.action
        if action == "create":
            data = await self._write("POST", "/version", op="jira.project.version.manage", json={**body, "projectId": int(proj.id)}, idempotent=False)
        elif action in ("update", "release", "unrelease", "archive"):
            if not change.version_id:
                raise InvalidRequestError(f"{action} needs version_id", hint="Get ids from list_versions.")
            flags = {"release": {"released": True}, "unrelease": {"released": False}, "archive": {"archived": True}}.get(action, {})
            data = await self._write("PUT", f"/version/{change.version_id}", op="jira.project.version.manage", json={**body, **flags})
        elif action == "delete":
            if not change.version_id:
                raise InvalidRequestError("delete needs version_id", hint="Get ids from list_versions.")
            await self._write("DELETE", f"/version/{change.version_id}", op="jira.project.version.manage")
            return Version(id=change.version_id, name=change.name or "")
        else:
            raise InvalidRequestError(f"unsupported version action {action!r}", hint="Use create, update, release, unrelease, archive or delete.")
        return n.version(data)

    # ---------------------------------------------------------------- admin (MANAGE)
    async def create_project(self, key: str, name: str, *, template: str, lead: str) -> Project:
        body = {"key": key, "name": name, "projectTypeKey": "software", "projectTemplateKey": template,
                "leadAccountId": await self._users.resolve(lead)}
        data = await self._write("POST", "/project", op="jira.project.create", json=body, idempotent=False)
        return Project(id=str(data["id"]), key=data.get("key", key), name=name, raw=data)

    async def update_project(self, key: str, **changes: Any) -> Project:
        data = await self._write("PUT", f"/project/{key}", op="jira.project.update", json=changes)
        return n.project(data)

    async def list_workflows(self, *, project: str | None = None) -> list[Workflow]:
        data = await self._json(J, "/workflow/search", params={"expand": "statuses"}, operation="jira.admin.workflows.list")
        return [Workflow(id=str((w.get("id") or {}).get("entityId", w.get("name", ""))), name=(w.get("id") or {}).get("name", w.get("name", "")),
                         statuses=[s.get("name", "") for s in w.get("statuses", [])], raw=w) for w in data.get("values", [])]

    async def get_workflow_editor_link(self, workflow_id: str) -> str:
        return f"{self.site_url}/jira/settings/issues/workflows/{workflow_id}"

    async def get_screen(self, screen_id: str) -> Screen:
        data = await self._json(J, "/screens", params={"id": screen_id}, operation="jira.admin.screen.get")
        match = next((s for s in data.get("values", []) if str(s.get("id")) == str(screen_id)), None)
        if match is None:
            raise NotFoundError(f"screen {screen_id} not found", operation="jira.admin.screen.get")
        return Screen(id=str(match["id"]), name=match.get("name"), raw=match)

    async def update_screen(self, screen_id: str, **changes: Any) -> Screen:
        data = await self._write("PUT", f"/screens/{screen_id}", op="jira.admin.screen.update", json=changes)
        return Screen(id=str(data.get("id", screen_id)), name=data.get("name"), raw=data)


__all__ = ["JiraWriteRest", "httpx"]
