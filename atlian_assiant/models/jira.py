"""Jira, Jira Software models (design section 8)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field as _F

from .common import AtlassianModel, UserRef


class Status(AtlassianModel):
    id: str
    name: str
    category: str | None = None           # "To Do" | "In Progress" | "Done"


class ProjectRef(AtlassianModel):
    id: str
    key: str
    name: str | None = None


class Issue(AtlassianModel):
    id: str
    key: str
    url: str | None = None
    summary: str | None = None
    status: Status | None = None
    issue_type: str | None = None
    project: ProjectRef | None = None
    priority: str | None = None
    assignee: UserRef | None = None
    reporter: UserRef | None = None
    labels: list[str] = _F(default_factory=list)
    parent_key: str | None = None
    resolution: str | None = None
    created: datetime | None = None
    updated: datetime | None = None
    description_markdown: str | None = None
    description_adf: dict[str, Any] | None = _F(default=None, repr=False)
    fields: dict[str, Any] = _F(default_factory=dict, repr=False)
    field_names: dict[str, str] = _F(default_factory=dict, repr=False)

    def to_agent_text(self, max_chars: int = 4000) -> str:
        from ..formats.render import truncate

        lines = [f"**{self.key}** {self.summary or ''}".rstrip()]
        meta = [("Status", self.status.name if self.status else None), ("Type", self.issue_type),
                ("Priority", self.priority), ("Assignee", self.assignee.display_name if self.assignee else None),
                ("Reporter", self.reporter.display_name if self.reporter else None),
                ("Labels", ", ".join(self.labels) or None), ("Updated", self.updated.isoformat() if self.updated else None)]
        lines += [f"- {k}: {v}" for k, v in meta if v]
        if self.description_markdown:
            lines += ["", self.description_markdown]
        return truncate("\n".join(lines), max_chars)[0]


class IssueDraft(AtlassianModel):
    project: str                                  # key
    issue_type: str                               # name or id
    summary: str
    description: str | None = None                # Markdown by default
    assignee: str | None = None                   # accountId, email or display name (resolved)
    labels: list[str] = _F(default_factory=list)
    parent: str | None = None
    fields: dict[str, Any] = _F(default_factory=dict)


class IssueUpdate(AtlassianModel):
    summary: str | None = None
    description: str | None = None
    labels_add: list[str] = _F(default_factory=list)
    labels_remove: list[str] = _F(default_factory=list)
    fields: dict[str, Any] = _F(default_factory=dict)


class CreatedIssue(AtlassianModel):
    id: str
    key: str
    url: str | None = None


class Comment(AtlassianModel):
    id: str
    author: UserRef | None = None
    body_markdown: str | None = None
    created: datetime | None = None
    updated: datetime | None = None
    visibility: dict[str, str] | None = None


class ChangelogItem(AtlassianModel):
    field: str
    from_string: str | None = None
    to_string: str | None = None


class ChangelogEntry(AtlassianModel):
    id: str
    author: UserRef | None = None
    created: datetime | None = None
    items: list[ChangelogItem] = _F(default_factory=list)


class Worklog(AtlassianModel):
    id: str
    author: UserRef | None = None
    time_spent: str | None = None
    time_spent_seconds: int | None = None
    started: datetime | None = None
    comment_markdown: str | None = None


class Transition(AtlassianModel):
    id: str
    name: str
    to_status: Status | None = None


class Attachment(AtlassianModel):
    id: str
    filename: str
    size: int | None = None
    mime_type: str | None = None
    author: UserRef | None = None
    created: datetime | None = None
    content_url: str | None = None


class AttachmentContent(AtlassianModel):
    attachment_id: str
    filename: str | None = None
    mime_type: str | None = None
    size: int = 0
    data: bytes | None = _F(default=None, exclude=True, repr=False)
    path: str | None = None
    text: str | None = None               # extracted text for agents when requested


class IssueLink(AtlassianModel):
    id: str
    link_type: str
    direction: str                        # "inward" | "outward"
    issue_key: str
    summary: str | None = None


class RemoteLink(AtlassianModel):
    id: str | None = None
    url: str
    title: str | None = None


class Project(AtlassianModel):
    id: str
    key: str
    name: str | None = None
    project_type: str | None = None
    lead: UserRef | None = None
    url: str | None = None


class Field(AtlassianModel):
    id: str
    name: str
    custom: bool = False
    schema_type: str | None = None
    required: bool | None = None



class IssueType(AtlassianModel):
    id: str
    name: str
    subtask: bool = False
    hierarchy_level: int | None = None


class Version(AtlassianModel):
    id: str
    name: str
    released: bool = False
    archived: bool = False
    release_date: str | None = None


class VersionChange(AtlassianModel):
    action: str                           # create | update | release | unrelease | archive | delete | merge | move
    version_id: str | None = None
    name: str | None = None
    release_date: str | None = None


class Component(AtlassianModel):
    id: str
    name: str
    lead: UserRef | None = None


class Filter(AtlassianModel):
    id: str
    name: str
    jql: str | None = None
    owner: UserRef | None = None


class Dashboard(AtlassianModel):
    id: str
    name: str
    url: str | None = None


class Board(AtlassianModel):
    id: int
    name: str
    type: str | None = None
    project_key: str | None = None


class BoardConfig(AtlassianModel):
    id: int
    name: str | None = None
    filter_id: str | None = None
    columns: list[dict[str, Any]] = _F(default_factory=list)
    estimation: dict[str, Any] | None = None


class Sprint(AtlassianModel):
    id: int
    name: str
    state: str | None = None              # future | active | closed
    start_date: datetime | None = None
    end_date: datetime | None = None
    goal: str | None = None


class SprintChange(AtlassianModel):
    action: str                           # create | update | start | close | delete
    sprint_id: int | None = None
    board_id: int | None = None
    name: str | None = None
    goal: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class Epic(AtlassianModel):
    id: int
    key: str
    name: str | None = None
    done: bool = False


class Workflow(AtlassianModel):
    id: str
    name: str
    statuses: list[str] = _F(default_factory=list)


class Screen(AtlassianModel):
    id: str
    name: str | None = None


class ServerInfo(AtlassianModel):
    base_url: str | None = None
    version: str | None = None
    deployment_type: str | None = None
    server_title: str | None = None
    time_zone: str | None = None


class JqlParseResult(AtlassianModel):
    query: str
    valid: bool
    errors: list[str] = _F(default_factory=list)
    structure: dict[str, Any] | None = None
