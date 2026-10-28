"""MCP write / delete / manage tools (design section 10.3). Same caveat as backend.py: argument names are best-effort."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ...core.errors import InvalidRequestError, NotFoundError
from ...models.common import BodyInput
from ...models.confluence import Attachment as CAttachment
from ...models.confluence import (
    ConfluenceComment,
    Content,
    ContentDraft,
    ContentUpdate,
    Label,
    Restrictions,
    Space,
    Task,
)
from ...models.jira import (
    Attachment,
    Board,
    Comment,
    CreatedIssue,
    IssueDraft,
    IssueUpdate,
    Project,
    RemoteLink,
    Screen,
    Sprint,
    SprintChange,
    Version,
    VersionChange,
    Workflow,
    Worklog,
)
from ...models.jsm import CustomerRequest, RequestComment
from ...protocols.confluence import ContentType
from ..rest_cloud import normalize as n
from ..rest_cloud.bodies import body_text
from .backend import McpClient, unsupported

WRITE_OP_TOOLS: dict[str, str] = {
    "jira.issue.create": "createJiraIssue", "jira.issue.edit": "editJiraIssue", "jira.issue.assign": "editJiraIssue",
    "jira.issue.transition": "transitionJiraIssue", "jira.issue.comment.add": "addOrEditJiraIssueComment",
    "jira.issue.comment.edit": "addOrEditJiraIssueComment", "jira.issue.worklog.add": "addOrEditJiraIssueWorklog",
    "jira.issue.link.create": "createJiraIssueLink", "jira.issue.remote_link.create": "createJiraIssueRemoteIssueLink",
    "jira.issue.attachment.upload": "uploadAttachmentToJiraIssue", "jira.issue.watch": "watchJiraIssue",
    "jira.issue.property.set": "editJiraEntityProperty", "jira.issue.hierarchy.convert": "convertJiraIssueHierarchy",
    "jira.issue.delete": "deleteJiraIssue", "jira.issue.comment.delete": "deleteJiraComment", "jira.issue.attachment.delete": "deleteJiraIssueAttachment",
    "jira.project.version.manage": "manageJiraProjectVersion", "jira.project.create": "createJiraProject", "jira.project.update": "updateJiraProject",
    "jira.admin.workflows.list": "getJiraWorkflows", "jira.admin.workflow.editor_link": "getJiraWorkflowEditorLink",
    "jira.admin.screen.get": "getJiraScreen", "jira.admin.screen.update": "updateJiraScreen",
    "jira.agile.sprint.manage": "manageJiraSprint", "jira.agile.issue.move_to_sprint": "manageJiraSprint", "jira.agile.issue.rank": "manageJiraSprint",
    "jira.agile.board.create": "createJiraBoard",
    "jsm.request.comment.add": "addOrEditJiraIssueComment", "jsm.request.transition": "transitionJiraIssue",
    "confluence.content.create": "createConfluenceContent", "confluence.content.update": "updateConfluenceContent",
    "confluence.content.move": "moveConfluenceContent", "confluence.content.copy": "copyConfluenceContent",
    "confluence.content.archive": "archiveConfluenceContent", "confluence.content.unarchive": "unarchiveConfluenceContent",
    "confluence.content.restore_version": "restoreConfluenceContentVersion", "confluence.content.set_status": "setConfluenceContentStatus",
    "confluence.label.add": "addLabelsToConfluenceContent", "confluence.comment.create": "createConfluenceComment",
    "confluence.comment.update": "updateConfluenceComment", "confluence.comment.resolve": "updateConfluenceCommentResolution",
    "confluence.attachment.upload": "createConfluenceAttachment", "confluence.permission.restrictions.set": "setConfluenceContentRestrictionState",
    "confluence.permission.add": "addConfluenceContentPermissions", "confluence.permission.remove": "removeConfluenceContentPermissions",
    "confluence.permission.replace": "replaceConfluenceContentPermissions", "confluence.content.public_link.set": "enableConfluencePublicLink",
    "confluence.task.complete": "completeConfluenceTask", "confluence.task.reopen": "reopenConfluenceTask",
    "confluence.star.content": "starConfluenceContent", "confluence.star.space": "starConfluenceSpace",
    "confluence.watch.content": "watchConfluenceContent", "confluence.watch.space": "watchConfluenceSpace", "confluence.watch.label": "watchConfluenceLabel",
    "confluence.reaction.add": "addConfluenceReaction", "confluence.space.create": "createConfluenceSpace",
    "confluence.space.instructions.set": "setConfluenceSpaceInstructions",
    "confluence.special.infographic.create": "createConfluenceInfographicForPage", "confluence.special.infographic.edit": "editConfluenceInfographicForPage",
    "confluence.special.maui_app.create": "createConfluenceMauiApp", "confluence.special.maui_app.edit": "editConfluenceMauiApp",
    "confluence.special.maui_app.get": "getConfluenceMauiApp", "graph.context.add": "addGraphContext",
}


#: tools that pair with a mapped one (the inverse action); exposed by the same permission group
PAIRED_TOOLS: frozenset[str] = frozenset({
    "disableConfluencePublicLink", "unwatchConfluenceContent", "unwatchConfluenceSpace", "unwatchConfluenceLabel", "unstarConfluenceContent",
    "unstarConfluenceSpace", "reopenConfluenceTask",
})


def _md(body: BodyInput | str | None) -> str:
    return body_text(body)


class McpJiraWrite(McpClient):
    """JiraIssueWriter, JiraIssueDeleter, JiraProjectWriter and JiraAdmin over MCP."""

    # ---- issue writer ----
    async def create_issue(self, draft: IssueDraft) -> CreatedIssue:
        args: dict[str, Any] = {"projectKey": draft.project, "issueType": draft.issue_type, "summary": draft.summary,
                                "description": draft.description, "assignee": draft.assignee, "labels": list(draft.labels) or None,
                                "parent": draft.parent, "additional_fields": dict(draft.fields) or None}
        data = await self.raw("createJiraIssue", args, op="jira.issue.create")
        return CreatedIssue(id=str(data["id"]), key=data["key"], url=f"{self.site_url}/browse/{data['key']}" if self.site_url else None, raw=data)

    async def bulk_create_issues(self, drafts: Sequence[IssueDraft]) -> list[CreatedIssue]:
        raise unsupported("jira.issue.bulk_create", "MCP creates one issue per call; use REST for atomic bulk creation")

    async def edit_issue(self, key: str, update: IssueUpdate, *, notify_users: bool = True) -> None:
        fields: dict[str, Any] = dict(update.fields)
        if update.summary is not None:
            fields["summary"] = update.summary
        if update.description is not None:
            fields["description"] = update.description
        if update.labels_add or update.labels_remove:
            current = (await self._issue(key, ["labels"])).get("fields", {}).get("labels", [])
            fields["labels"] = [x for x in current if x not in update.labels_remove] + [x for x in update.labels_add if x not in current]
        if not fields:
            raise InvalidRequestError("nothing to update", hint="Set at least one of summary, description, labels or fields.")
        if not notify_users:
            raise unsupported("jira.issue.edit", "editJiraIssue cannot suppress notifications; REST honours notify_users=False")
        await self.raw("editJiraIssue", {"issueIdOrKey": key, "fields": fields}, op="jira.issue.edit")

    async def _issue(self, key: str, fields: list[str]) -> dict[str, Any]:
        data = await self.raw("getJiraIssue", {"issueIdOrKey": key, "fields": fields}, op="jira.issue.get")
        return dict(data)

    async def assign_issue(self, key: str, assignee: str | None) -> None:
        await self.raw("editJiraIssue", {"issueIdOrKey": key, "fields": {"assignee": {"accountId": assignee} if assignee else None}}, op="jira.issue.assign")

    async def transition_issue(self, key: str, transition: str, *, fields: dict[str, Any] | None = None, comment: BodyInput | str | None = None) -> None:
        options = await self.raw("listJiraIssueTransitions", {"issueIdOrKey": key}, op="jira.issue.transition")
        options = options if isinstance(options, list) else options.get("transitions", [])
        match = [t for t in options if transition in (str(t.get("id")), t.get("name")) or str(t.get("name", "")).lower() == transition.lower()]
        if not match:
            raise NotFoundError(f"transition {transition!r} is not available for {key}", hint="Available: " + ", ".join(str(t.get("name")) for t in options),
                                operation="jira.issue.transition")
        await self.raw("transitionJiraIssue", {"issueIdOrKey": key, "transitionId": str(match[0]["id"]), "fields": fields or None}, op="jira.issue.transition")
        if comment:                           # the tool drops comments passed with a transition: add it separately (documented by the tool)
            await self.raw("addOrEditJiraIssueComment", {"issueIdOrKey": key, "commentBody": _md(comment)}, op="jira.issue.transition")

    async def add_comment(self, key: str, body: BodyInput | str, *, visibility: dict[str, str] | None = None) -> Comment:
        data = await self.raw("addOrEditJiraIssueComment", {"issueIdOrKey": key, "commentBody": _md(body), "commentVisibility": visibility},
                              op="jira.issue.comment.add")
        return n.comment(data)

    async def edit_comment(self, key: str, comment_id: str, body: BodyInput | str) -> Comment:
        data = await self.raw("addOrEditJiraIssueComment", {"issueIdOrKey": key, "commentId": comment_id, "commentBody": _md(body)}, op="jira.issue.comment.edit")
        return n.comment(data)

    async def add_worklog(self, key: str, time_spent: str, *, started: str | None = None, comment: BodyInput | str | None = None) -> Worklog:
        data = await self.raw("addOrEditJiraIssueWorklog", {"issueIdOrKey": key, "timeSpent": time_spent, "started": started, "comment": _md(comment) or None},
                              op="jira.issue.worklog.add")
        return n.worklog(data)

    async def create_link(self, link_type: str, inward_key: str, outward_key: str) -> None:
        await self.raw("createJiraIssueLink", {"linkType": link_type, "inwardIssue": inward_key, "outwardIssue": outward_key}, op="jira.issue.link.create")

    async def create_remote_link(self, key: str, url: str, title: str) -> RemoteLink:
        data = await self.raw("createJiraIssueRemoteIssueLink", {"issueIdOrKey": key, "url": url, "title": title}, op="jira.issue.remote_link.create")
        return RemoteLink(id=str(data.get("id", "")) or None, url=url, title=title, raw=data if isinstance(data, dict) else None)

    async def upload_attachment(self, key: str, file: Path | bytes, *, filename: str | None = None) -> list[Attachment]:
        raise unsupported("jira.issue.attachment.upload", "the v2 tool reads a file path on the MCP server side; REST uploads the bytes")

    async def watch_issue(self, key: str, *, watch: bool = True, account_id: str | None = None) -> None:
        if account_id:
            raise unsupported("jira.issue.watch", "watchJiraIssue only (un)watches as the calling user")
        await self.raw("watchJiraIssue", {"issueIdOrKey": key, "action": "watch" if watch else "unwatch"}, op="jira.issue.watch")

    async def set_property(self, key: str, property_key: str, value: Any) -> None:
        await self.raw("editJiraEntityProperty", {"entityType": "issue", "issueIdOrKey": key, "propertyKey": property_key, "value": value},
                       op="jira.issue.property.set")

    async def convert_hierarchy(self, key: str, *, new_issue_type: str, new_parent: str | None = None) -> None:
        await self.raw("convertJiraIssueHierarchy", {"issueIdOrKey": key, "targetIssueType": new_issue_type, "parentIssueKey": new_parent, "mode": "confirm"},
                       op="jira.issue.hierarchy.convert")

    # ---- deleter ----
    async def delete_issue(self, key: str, *, delete_subtasks: bool = False) -> None:
        await self.raw("deleteJiraIssue", {"issueIdOrKey": key, "deleteSubtasks": delete_subtasks}, op="jira.issue.delete")

    async def delete_comment(self, key: str, comment_id: str) -> None:
        await self.raw("deleteJiraComment", {"issueIdOrKey": key, "commentId": comment_id}, op="jira.issue.comment.delete")

    async def delete_attachment(self, attachment_id: str) -> None:
        await self.raw("deleteJiraIssueAttachment", {"attachmentId": attachment_id}, op="jira.issue.attachment.delete")

    # ---- project writer / admin ----
    async def manage_version(self, project: str, change: VersionChange) -> Version:
        data = await self.raw("manageJiraProjectVersion", {"projectIdOrKey": project, "action": change.action, "versionId": change.version_id,
                                                           "name": change.name, "releaseDate": change.release_date}, op="jira.project.version.manage")
        return n.version(data if isinstance(data, dict) and data else {"id": change.version_id or "", "name": change.name or ""})

    async def create_project(self, key: str, name: str, *, template: str, lead: str) -> Project:
        data = await self.raw("createJiraProject", {"key": key, "name": name, "templateKey": template, "leadAccountId": lead}, op="jira.project.create")
        return Project(id=str(data.get("id", "")), key=data.get("key", key), name=name, raw=data)

    async def update_project(self, key: str, **changes: Any) -> Project:
        return n.project(await self.raw("updateJiraProject", {"projectIdOrKey": key, **changes}, op="jira.project.update"))

    async def list_workflows(self, *, project: str | None = None) -> list[Workflow]:
        data = await self.raw("getJiraWorkflows", {"projectIdOrKey": project}, op="jira.admin.workflows.list")
        return [Workflow(id=str(w.get("id", "")), name=w.get("name", ""), statuses=list(w.get("statuses", [])), raw=w)
                for w in (data if isinstance(data, list) else data.get("workflows", []))]

    async def get_workflow_editor_link(self, workflow_id: str) -> str:
        data = await self.raw("getJiraWorkflowEditorLink", {"workflowId": workflow_id}, op="jira.admin.workflow.editor_link")
        return str(data.get("url") if isinstance(data, dict) else data)

    async def get_screen(self, screen_id: str) -> Screen:
        data = await self.raw("getJiraScreen", {"screenId": screen_id}, op="jira.admin.screen.get")
        return Screen(id=str(data.get("id", screen_id)), name=data.get("name"), raw=data)

    async def update_screen(self, screen_id: str, **changes: Any) -> Screen:
        data = await self.raw("updateJiraScreen", {"screenId": screen_id, **changes}, op="jira.admin.screen.update")
        return Screen(id=str(data.get("id", screen_id)), name=data.get("name"), raw=data)


class McpAgileWrite(McpClient):
    async def manage_sprint(self, change: SprintChange) -> Sprint:
        data = await self.raw("manageJiraSprint", {"action": change.action, "sprintId": change.sprint_id, "boardId": change.board_id, "name": change.name,
                                                   "goal": change.goal, "startDate": change.start_date, "endDate": change.end_date}, op="jira.agile.sprint.manage")
        return n.sprint(data)

    async def move_issues_to_sprint(self, sprint_id: int, issue_keys: Sequence[str]) -> None:
        await self.raw("manageJiraSprint", {"action": "addIssues", "sprintId": sprint_id, "issueIdsOrKeys": list(issue_keys)}, op="jira.agile.issue.move_to_sprint")

    async def move_issues_to_backlog(self, issue_keys: Sequence[str]) -> None:
        raise unsupported("jira.agile.issue.move_to_sprint", "manageJiraSprint has no move-to-backlog action")

    async def rank_issues(self, issue_keys: Sequence[str], *, before: str | None = None, after: str | None = None) -> None:
        if bool(before) == bool(after):
            raise InvalidRequestError("rank needs exactly one of before / after", hint="Pass the key to rank before or after.")
        for key in issue_keys:                # editJiraIssue ranks one issue at a time
            await self.raw("editJiraIssue", {"issueIdOrKey": key, "rankBeforeIssue": before, "rankAfterIssue": after}, op="jira.agile.issue.rank")

    async def create_board(self, name: str, *, filter_id: int, board_type: str = "scrum") -> Board:
        return n.board(await self.raw("createJiraBoard", {"name": name, "filterId": filter_id, "type": board_type}, op="jira.agile.board.create"))


class McpJsmWrite(McpClient):
    """JSM writes through MCP are partial: comments and transitions work (a request is a Jira issue); the rest has no tool."""

    async def create_request(self, service_desk_id: str, request_type_id: str, fields: dict[str, Any], *, on_behalf_of: str | None = None) -> CustomerRequest:
        raise unsupported("jsm.request.create")

    async def add_request_comment(self, key: str, body: BodyInput | str, *, public: bool) -> RequestComment:
        data = await self.raw("addOrEditJiraIssueComment", {"issueIdOrKey": key, "commentBody": _md(body), "jsmCommentType": "publicComment" if public else "internalNote"}, op="jsm.request.comment.add")
        return RequestComment(id=str(data.get("id", "")), body=_md(body), public=public, raw=data if isinstance(data, dict) else None)

    async def transition_request(self, key: str, transition: str, *, comment: BodyInput | str | None = None) -> None:
        options = await self.raw("listJiraIssueTransitions", {"issueIdOrKey": key}, op="jsm.request.transition")
        options = options if isinstance(options, list) else options.get("transitions", [])
        match = [t for t in options if transition in (str(t.get("id")), t.get("name"))]
        if not match:
            raise NotFoundError(f"transition {transition!r} is not available for {key}", hint="Available: " + ", ".join(str(t.get("name")) for t in options),
                                operation="jsm.request.transition")
        await self.raw("transitionJiraIssue", {"issueIdOrKey": key, "transitionId": str(match[0]["id"])}, op="jsm.request.transition")
        if comment:
            await self.raw("addOrEditJiraIssueComment", {"issueIdOrKey": key, "commentBody": _md(comment)}, op="jsm.request.transition")

    async def answer_approval(self, key: str, approval_id: str, *, approve: bool) -> None:
        raise unsupported("jsm.request.approve")

    async def add_customers(self, service_desk_id: str, account_ids: list[str]) -> None:
        raise unsupported("jsm.customer.add")


class McpConfluenceWrite(McpClient):
    """ConfluenceContentWriter, ConfluenceSpaceWriter, ConfluencePermissionWriter and the MCP-only special-content tools."""

    def _content(self, d: dict[str, Any], type_: ContentType = ContentType.PAGE) -> Content:
        return n.content(d, type_=type_.value, base=f"{self.site_url}/wiki" if self.site_url else None)

    async def create_content(self, draft: ContentDraft) -> Content:
        if not str(draft.space).isdigit() and not draft.parent_id:
            raise unsupported("confluence.content.create", "createConfluenceContent needs a numeric space id or a parent content id")
        parent = {"parentContentId": draft.parent_id} if draft.parent_id else {"spaceId": str(draft.space)}
        data = await self.raw("createConfluenceContent", {"parent": parent, "title": draft.title, "body": {"format": "markdown", "value": _md(draft.body)},
                                                          "contentType": draft.type, "draft": draft.status == "draft" or None}, op="confluence.content.create")
        return self._content(data, ContentType(draft.type))

    async def update_content(self, content_id: str, update: ContentUpdate) -> Content:
        if update.status is not None:
            raise unsupported("confluence.content.update", "updateConfluenceContent cannot change status; use set_status")
        current = await self.raw("getConfluenceContent", {"content_id": content_id}, op="confluence.content.update")   # snapshotToken = optimistic lock
        now = ((current.get("metadata") or {}).get("version") or {}).get("number")
        if update.version is not None and now is not None and update.version != now:
            from ...core.errors import ConflictError

            raise ConflictError(f"Page version changed: you based this on v{update.version} but it is now v{now}.", status=409,
                                operation="confluence.content.update", hint="Re-fetch the page and retry with version = current.")
        data = await self.raw("updateConfluenceContent", {"contentId": content_id, "snapshotToken": current.get("snapshotToken"), "title": update.title,
                                                          "body": {"format": "markdown", "value": _md(update.body)} if update.body is not None else None,
                                                          "versionMessage": update.message}, op="confluence.content.update")
        return self._content(data)

    async def move_content(self, content_id: str, *, target_parent_id: str | None = None, target_space: str | None = None, position: str = "append") -> Content:
        if not target_parent_id and not target_space:
            raise InvalidRequestError("move needs target_parent_id or target_space", hint="Pass the new parent page id.")
        if not target_parent_id:
            raise unsupported("confluence.content.move", "moveConfluenceContent moves relative to a target content id, not to a space")
        return self._content(await self.raw("moveConfluenceContent", {"id": content_id, "targetId": target_parent_id, "position": position},
                                            op="confluence.content.move"))

    async def copy_content(self, content_id: str, *, destination_parent_id: str, title: str | None = None) -> Content:
        return self._content(await self.raw("copyConfluenceContent", {"contentId": content_id, "parentContentId": destination_parent_id, "title": title},
                                            op="confluence.content.copy"))

    async def archive_content(self, content_ids: Sequence[str]) -> None:
        for content_id in content_ids:
            await self.raw("archiveConfluenceContent", {"contentId": content_id}, op="confluence.content.archive")

    async def unarchive_content(self, content_ids: Sequence[str]) -> None:
        for content_id in content_ids:
            await self.raw("unarchiveConfluenceContent", {"contentId": content_id}, op="confluence.content.unarchive")

    async def restore_version(self, content_id: str, version: int, *, message: str | None = None) -> Content:
        return self._content(await self.raw("restoreConfluenceContentVersion", {"contentId": content_id, "versionNumber": version, "message": message},
                                            op="confluence.content.restore_version"))

    async def set_status(self, content_id: str, status: str) -> None:
        await self.raw("setConfluenceContentStatus", {"contentId": content_id, "status": status}, op="confluence.content.set_status")

    async def add_labels(self, content_id: str, labels: Sequence[str]) -> list[Label]:
        data = await self.raw("addLabelsToConfluenceContent", {"contentId": content_id, "labels": list(labels)}, op="confluence.label.add")
        items = data if isinstance(data, list) else data.get("labels", data.get("results", []))
        return [n.label(x) if isinstance(x, dict) else Label(name=str(x)) for x in items]

    async def remove_label(self, content_id: str, label: str) -> None:
        raise unsupported("confluence.label.remove", "the MCP catalog can add labels but not remove them")

    async def add_comment(self, content_id: str, body: BodyInput | str, *, parent_comment_id: str | None = None, inline_anchor: dict[str, Any] | None = None) -> ConfluenceComment:
        top = {} if parent_comment_id else {"contentId": content_id, "commentType": "inline" if inline_anchor else "footer"}
        data = await self.raw("createConfluenceComment", {**top, "body": {"format": "markdown", "value": _md(body)}, "parentCommentId": parent_comment_id,
                                                          "inlineSelection": inline_anchor}, op="confluence.comment.create")
        return n.conf_comment(data, "inline" if inline_anchor else "footer")

    async def update_comment(self, comment_id: str, body: BodyInput | str) -> ConfluenceComment:
        return n.conf_comment(await self.raw("updateConfluenceComment", {"commentId": comment_id, "body": {"format": "markdown", "value": _md(body)}}, op="confluence.comment.update"), "footer")

    async def resolve_comment(self, comment_id: str, *, resolved: bool = True) -> None:
        await self.raw("updateConfluenceCommentResolution", {"commentId": comment_id, "resolved": resolved}, op="confluence.comment.resolve")

    async def upload_attachment(self, content_id: str, file: Path | bytes, *, filename: str | None = None, comment: str | None = None) -> CAttachment:
        raise unsupported("confluence.attachment.upload", "the v2 tool reads a file path on the MCP server side; REST uploads the bytes")

    async def set_property(self, content_id: str, key: str, value: Any) -> None:
        raise unsupported("confluence.property.set")

    async def set_restrictions(self, content_id: str, restrictions: Restrictions) -> None:
        if restrictions.read_users or restrictions.read_groups:
            state = "VIEW_RESTRICTED"
        elif restrictions.update_users or restrictions.update_groups:
            state = "EDIT_RESTRICTED"
        else:
            state = "OPEN"
        await self.raw("setConfluenceContentRestrictionState", {"contentId": content_id, "state": state}, op="confluence.permission.restrictions.set")
        grants = [{"operation": op_, "principals": [*({"principalType": "user", "principalId": u.account_id} for u in users),
                                                     *({"principalType": "group", "principalId": g} for g in groups)]}
                  for op_, users, groups in (("read", restrictions.read_users, restrictions.read_groups),
                                             ("update", restrictions.update_users, restrictions.update_groups)) if users or groups]
        if grants:
            await self.raw("replaceConfluenceContentPermissions", {"contentId": content_id, "permissions": grants}, op="confluence.permission.restrictions.set")

    async def set_task_status(self, task_id: str, *, complete: bool) -> Task:
        tool = "completeConfluenceTask" if complete else "reopenConfluenceTask"
        return n.conf_task(await self.raw(tool, {"taskId": task_id}, op="confluence.task.complete"))

    async def watch(self, *, content_id: str | None = None, space_key: str | None = None, label: str | None = None, watch: bool = True) -> None:
        if not (content_id or space_key or label):
            raise InvalidRequestError("watch needs content_id, space_key or label", hint="Pass exactly one.")
        kind = "Content" if content_id else "Space" if space_key else "Label"
        tool = f"{'watch' if watch else 'unwatch'}Confluence{kind}"
        await self.raw(tool, {"contentId": content_id, "spaceKey": space_key, "label": label}, op=f"confluence.watch.{kind.lower()}")

    async def star(self, *, content_id: str | None = None, space_key: str | None = None, star: bool = True) -> None:
        kind = "Content" if content_id else "Space"
        await self.raw(f"{'star' if star else 'unstar'}Confluence{kind}", {"contentId": content_id, "spaceKey": space_key}, op=f"confluence.star.{kind.lower()}")

    async def add_reaction(self, content_id: str, emoji: str) -> None:
        await self.raw("addConfluenceReaction", {"contentId": content_id, "emojiId": emoji if re.fullmatch(r"[0-9a-f-]{4,}", emoji) else format(ord(emoji[0]), "x")}, op="confluence.reaction.add")

    async def create_space(self, key: str, name: str, *, description: str | None = None) -> Space:
        return n.space(await self.raw("createConfluenceSpace", {"key": key, "name": name, "description": description}, op="confluence.space.create"))

    async def set_space_instructions(self, key_or_id: str, instructions: str) -> None:
        ref = {"spaceId": key_or_id} if key_or_id.isdigit() else {"spaceKey": key_or_id}
        await self.raw("setConfluenceSpaceInstructions", {**ref, "body": instructions}, op="confluence.space.instructions.set")

    async def add_permissions(self, content_id: str, grants: Sequence[Mapping[str, Any]]) -> None:
        await self.raw("addConfluenceContentPermissions", {"contentId": content_id, "permissions": list(grants)}, op="confluence.permission.add")

    async def remove_permissions(self, content_id: str, grants: Sequence[Mapping[str, Any]]) -> None:
        for grant in grants:                  # the tool removes by filter: one call per (operation, principal)
            for p in grant.get("principals") or [{}]:
                await self.raw("removeConfluenceContentPermissions", {"contentId": content_id, "operation": grant.get("operation"),
                                                                      "principal-type": p.get("principalType"), "principal-id": p.get("principalId")},
                               op="confluence.permission.remove")

    async def replace_permissions(self, content_id: str, grants: Sequence[Mapping[str, Any]]) -> None:
        await self.raw("replaceConfluenceContentPermissions", {"contentId": content_id, "permissions": list(grants)}, op="confluence.permission.replace")

    async def set_public_link(self, content_id: str, *, enabled: bool) -> None:
        await self.raw("enableConfluencePublicLink" if enabled else "disableConfluencePublicLink", {"contentId": content_id}, op="confluence.content.public_link.set")

    # deletes: not in the MCP catalog
    async def delete_content(self, content_id: str, *, type: ContentType = ContentType.PAGE, purge: bool = False) -> None:
        raise unsupported("confluence.content.delete")

    async def delete_comment(self, comment_id: str) -> None:
        raise unsupported("confluence.comment.delete")

    async def delete_attachment(self, attachment_id: str, *, purge: bool = False) -> None:
        raise unsupported("confluence.attachment.delete")

    # ---- MCP-only special content ----
    async def get_maui_app(self, app_id: str) -> dict[str, Any]:
        return dict(await self.raw("getConfluenceMauiApp", {"appId": app_id}, op="confluence.special.maui_app.get"))

    async def create_infographic(self, content_id: str, *, prompt: str) -> dict[str, Any]:
        return dict(await self.raw("createConfluenceInfographicForPage", {"contentId": content_id, "prompt": prompt}, op="confluence.special.infographic.create"))

    async def edit_infographic(self, content_id: str, infographic_id: str, *, prompt: str) -> dict[str, Any]:
        return dict(await self.raw("editConfluenceInfographicForPage", {"contentId": content_id, "infographicId": infographic_id, "prompt": prompt},
                                   op="confluence.special.infographic.edit"))

    async def create_maui_app(self, content_id: str, *, spec: str) -> dict[str, Any]:
        return dict(await self.raw("createConfluenceMauiApp", {"contentId": content_id, "spec": spec}, op="confluence.special.maui_app.create"))

    async def edit_maui_app(self, app_id: str, *, spec: str) -> dict[str, Any]:
        return dict(await self.raw("editConfluenceMauiApp", {"appId": app_id, "spec": spec}, op="confluence.special.maui_app.edit"))
