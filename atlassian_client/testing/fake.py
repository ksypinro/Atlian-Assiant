"""FakeBackend: an in-memory implementation of every protocol (design section 18).

Seeded from the same raw dataset as the HTTP mock, so contract suites assert identical behaviour on every backend.
Like the REST backend it is composed of one component per API family (see ``protocols.base.implementation_of``).
Writers mutate the in-memory state; unsupported provider-specific features raise ``UnsupportedOperationError``.
"""
from __future__ import annotations

import difflib
import itertools
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal, TypeVar

from ..backends.mcp.backend import ALL_OP_TOOLS
from ..backends.rest_cloud import normalize as n
from ..backends.rest_cloud.backend import SUPPORTED
from ..capabilities import BackendKind, Deployment
from ..core.errors import ConflictError, NotFoundError, UnsupportedOperationError
from ..core.pagination import Cursor
from ..formats.adf import adf_to_markdown
from ..models.admin import AuditEvent, AuditEventPage
from ..models.common import BodyInput, Page, UserRef
from ..models.confluence import Attachment as CAttachment
from ..models.confluence import AttachmentContent as CAttachmentContent
from ..models.confluence import (
    ConfluenceComment,
    Content,
    ContentDiff,
    ContentDraft,
    ContentUpdate,
    ContentVersion,
    Label,
    Permissions,
    Restrictions,
    SearchHit,
    Space,
    Task,
    Template,
)
from ..models.graph import GraphContext, GraphObject, SemanticHit
from ..models.identity import AccessibleResource, Identity
from ..models.jira import (
    Attachment,
    AttachmentContent,
    Board,
    BoardConfig,
    ChangelogEntry,
    Comment,
    Component,
    CreatedIssue,
    Dashboard,
    Epic,
    Field,
    Filter,
    Issue,
    IssueDraft,
    IssueLink,
    IssueType,
    IssueUpdate,
    JqlParseResult,
    Project,
    RemoteLink,
    Screen,
    ServerInfo,
    Sprint,
    SprintChange,
    Status,
    Transition,
    Version,
    VersionChange,
    Workflow,
    Worklog,
)
from ..models.jsm import (
    CustomerRequest,
    JsmInfo,
    KbArticle,
    Organization,
    RequestComment,
    RequestType,
    ServiceDesk,
    SlaInfo,
)
from ..models.webhooks import FailedWebhook, RawResponse, WebhookRegistration, WebhookSubscription
from ..protocols.base import BackendHealth
from ..protocols.confluence import BodyFormat, ContentType
from .scenario import scenario

T = TypeVar("T")


def _body_text(body: BodyInput | str | None) -> str:
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    return adf_to_markdown(body.value) if isinstance(body.value, dict) else str(body.value)


class _Base:
    def __init__(self, store: FakeStore) -> None:
        self.s = store

    def page(self, items: Sequence[T], limit: int, cursor: str | None) -> Page[T]:
        start = int(Cursor.decode(cursor).state["i"]) if cursor else 0
        chunk = list(items[start:start + limit])
        nxt = Cursor(self.s.kind, "fake", {"i": start + limit}).encode() if start + limit < len(items) else None
        return Page(items=chunk, next_cursor=nxt)


class FakeStore:
    """All state, normalized from the raw scenario."""

    def __init__(self, raw: dict[str, Any], kind: BackendKind) -> None:
        self.kind, self.raw = kind, raw
        j, a, s, c = raw["jira"], raw["agile"], raw["jsm"], raw["confluence"]
        self.me = n.user(j["myself"]) or UserRef()
        self.users = [u for u in (n.user(x) for x in j["users"]) if u]
        self.issues = {i["key"]: n.issue(i, "https://example.atlassian.net") for i in j["issues"]}
        self.comments = {k: [n.comment(x) for x in v] for k, v in j["comments"].items()}
        self.changelog = {k: [n.changelog(x) for x in v] for k, v in j["changelog"].items()}
        self.worklogs = {k: [n.worklog(x) for x in v] for k, v in j["worklogs"].items()}
        self.transitions = {k: [n.transition(x) for x in v] for k, v in j["transitions"].items()}
        self.remote_links = {k: [n.remote_link(x) for x in v] for k, v in j["remote_links"].items()}
        self.watchers = {k: [u for u in (n.user(x) for x in v) if u] for k, v in j["watchers"].items()}
        self.properties = {k: {pk: pv["value"] for pk, pv in v.items()} for k, v in j["properties"].items()}
        self.attachment_bytes = dict(j["attachment_content"])
        self.projects = [n.project(p) for p in j["projects"]]
        self.components = {k: [n.component(x) for x in v] for k, v in j["components"].items()}
        self.versions = {k: [n.version(x) for x in v] for k, v in j["versions"].items()}
        self.project_statuses = j["project_statuses"]
        self.fields = [n.jfield(f) for f in j["fields"]]
        self.issue_types = [n.issue_type(t) for t in j["issue_types"]]
        self.statuses = [st for st in (n.status(x) for x in j["statuses"]) if st]
        self.priorities = [p["name"] for p in j["priorities"]]
        self.link_types = [t["name"] for t in j["link_types"]]
        self.server_info = n.server_info(j["server_info"])
        self.create_fields = j["create_fields"]
        self.filters = [n.jfilter(f) for f in j["filters"]]
        self.dashboards = [n.dashboard(d) for d in j["dashboards"]]
        self.boards = [n.board(b) for b in a["boards"]]
        self.board_configs = {k: n.board_config(v) for k, v in a["configs"].items()}
        self.board_issues = {k: [n.issue(i, "https://example.atlassian.net") for i in v] for k, v in a["board_issues"].items()}
        self.sprints = {k: [n.sprint(x) for x in v] for k, v in a["sprints"].items()}
        self.sprint_issues = {k: [n.issue(i, "https://example.atlassian.net") for i in v] for k, v in a["sprint_issues"].items()}
        self.backlog = {k: [n.issue(i, "https://example.atlassian.net") for i in v] for k, v in a["backlog"].items()}
        self.epics = {k: [n.epic(x) for x in v] for k, v in a["epics"].items()}
        self.jsm_info = n.jsm_info(s["info"])
        self.service_desks = [n.service_desk(x) for x in s["service_desks"]]
        self.request_types = {k: [n.request_type(x) for x in v] for k, v in s["request_types"].items()}
        self.requests = [n.customer_request(x) for x in s["requests"]]
        self.request_comments = {k: [n.request_comment(x) for x in v] for k, v in s["comments"].items()}
        self.sla = {k: [n.sla(x) for x in v] for k, v in s["sla"].items()}
        self.kb = [n.kb_article(x) for x in s["kb"]]
        self.organizations = [n.organization(x) for x in s["organizations"]]
        self.customers = {k: [u for u in (n.user(x) for x in v) if u] for k, v in s["customers"].items()}
        base = "https://example.atlassian.net/wiki"
        self.spaces = [n.space(x) for x in c["spaces"]]
        self.pages = {k: n.content(v, base=base) for k, v in c["pages"].items()}
        self.page_versions = c["page_versions"]
        self.children, self.ancestors, self.descendants = c["children"], c["ancestors"], c["descendants"]
        self.content_versions = {k: [n.content_version(x) for x in v] for k, v in c["versions"].items()}
        self.labels = {k: [n.label(x) for x in v] for k, v in c["labels"].items()}
        self.content_properties = {k: {p["key"]: p["value"] for p in v} for k, v in c["properties"].items()}
        self.footer = {k: [n.conf_comment(x, "footer") for x in v] for k, v in c["footer_comments"].items()}
        self.inline = {k: [n.conf_comment(x, "inline") for x in v] for k, v in c["inline_comments"].items()}
        self.conf_attachments = {k: [n.conf_attachment(x) for x in v] for k, v in c["attachments"].items()}
        self.conf_attachment_bytes = dict(c["attachment_content"])
        self.operations = c["operations"]
        self.restrictions = {k: n.restrictions(v) for k, v in c["restrictions"].items()}
        self.tasks = [n.conf_task(x) for x in c["tasks"]]
        self.templates = [n.template(x) for x in c["templates"]]
        self.search = [n.search_hit(x) for x in c["search"]]
        self.counter = itertools.count(1000)


def _find(mapping: Mapping[str, T], key: str, what: str) -> T:
    if key not in mapping:
        raise NotFoundError(f"{what} {key} not found", hint="Check the id/key, or you may lack permission.")
    return mapping[key]


class FakeJira(_Base):
    """Jira Platform: reader, searcher, writer, deleter, project reader/writer, metadata, users, JQL tools, admin."""

    # ---- reader ----
    async def get_issue(self, key: str, *, fields: Sequence[str] | None = None, expand: Sequence[str] = ()) -> Issue:
        issue = _find(self.s.issues, key.upper(), "issue").model_copy(deep=True)
        issue.field_names = {f.id: f.name for f in self.s.fields}      # like REST, which sends expand=names
        return issue

    async def list_comments(self, key: str, *, limit: int = 50, cursor: str | None = None) -> Page[Comment]:
        _find(self.s.issues, key.upper(), "issue")
        return self.page(self.s.comments.get(key.upper(), []), limit, cursor)

    async def list_changelog(self, key: str, *, limit: int = 100, cursor: str | None = None) -> Page[ChangelogEntry]:
        _find(self.s.issues, key.upper(), "issue")
        return self.page(self.s.changelog.get(key.upper(), []), limit, cursor)

    async def list_worklogs(self, key: str, *, limit: int = 100, cursor: str | None = None) -> Page[Worklog]:
        _find(self.s.issues, key.upper(), "issue")
        return self.page(self.s.worklogs.get(key.upper(), []), limit, cursor)

    async def list_transitions(self, key: str) -> list[Transition]:
        _find(self.s.issues, key.upper(), "issue")
        return list(self.s.transitions.get(key.upper(), []))

    async def list_attachments(self, key: str) -> list[Attachment]:
        issue = _find(self.s.issues, key.upper(), "issue")
        return [n.attachment(a) for a in issue.fields.get("attachment", [])]

    async def download_attachment(self, attachment_id: str, *, dest: Path | None = None, max_bytes: int | None = None,
                                  extract_text: bool = False) -> AttachmentContent:
        data = _find(self.s.attachment_bytes, attachment_id, "attachment")
        if max_bytes is not None and len(data) > max_bytes:
            raise ConflictError(f"attachment is {len(data)} bytes, above max_bytes={max_bytes}", status=413)
        meta: dict[str, Any] = next((a for i in self.s.issues.values() for a in i.fields.get("attachment", []) if str(a["id"]) == attachment_id), {})
        mime = meta.get("mimeType")
        text = data.decode("utf-8", "replace") if extract_text and (mime or "").startswith("text/") else None
        path = None
        if dest is not None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            path = str(dest)
        return AttachmentContent(attachment_id=attachment_id, filename=meta.get("filename"), mime_type=mime, size=len(data),
                                 data=None if dest else data, path=path, text=text)

    async def list_links(self, key: str) -> list[IssueLink]:
        issue = _find(self.s.issues, key.upper(), "issue")
        return [n.issue_link(x) for x in issue.fields.get("issuelinks", [])]

    async def list_remote_links(self, key: str) -> list[RemoteLink]:
        _find(self.s.issues, key.upper(), "issue")
        return list(self.s.remote_links.get(key.upper(), []))

    async def list_watchers(self, key: str) -> list[UserRef]:
        _find(self.s.issues, key.upper(), "issue")
        return list(self.s.watchers.get(key.upper(), []))

    async def get_property(self, key: str, property_key: str) -> Any:
        return _find(self.s.properties.get(key.upper(), {}), property_key, "property")

    # ---- searcher ----
    async def search_issues(self, jql: str, *, fields: Sequence[str] | None = None, limit: int = 50, cursor: str | None = None,
                            reconcile_issue_ids: Sequence[int] = ()) -> Page[Issue]:
        return self.page(list(self.s.issues.values()), limit, cursor)

    async def count_issues(self, jql: str) -> int:
        return len(self.s.issues)

    # ---- writer ----
    async def create_issue(self, draft: IssueDraft) -> CreatedIssue:
        from ..core.errors import InvalidRequestError

        if not draft.summary.strip():
            raise InvalidRequestError("You must specify a summary of the issue.", hint="Set IssueDraft.summary.")
        num = next(self.s.counter)
        key = f"{draft.project}-{num}"
        self.s.issues[key] = Issue(id=str(10000 + num), key=key, summary=draft.summary, issue_type=draft.issue_type,
                                   description_markdown=draft.description, labels=list(draft.labels), parent_key=draft.parent,
                                   fields={"summary": draft.summary})
        return CreatedIssue(id=str(10000 + num), key=key)

    async def bulk_create_issues(self, drafts: Sequence[IssueDraft]) -> list[CreatedIssue]:
        from ..core.errors import InvalidRequestError

        if not 1 <= len(drafts) <= 50:
            raise InvalidRequestError("bulk create accepts 1 to 50 issues per call", hint="Split the list into chunks of at most 50.")
        return [await self.create_issue(d) for d in drafts]

    async def edit_issue(self, key: str, update: IssueUpdate, *, notify_users: bool = True) -> None:
        from ..core.errors import InvalidRequestError

        if not (update.summary or update.description is not None or update.labels_add or update.labels_remove or update.fields):
            raise InvalidRequestError("nothing to update", hint="Set at least one of summary, description, labels or fields.")
        issue = _find(self.s.issues, key.upper(), "issue")
        if update.summary is not None:
            issue.summary = update.summary
        if update.description is not None:
            issue.description_markdown = update.description
        issue.labels = [lb for lb in issue.labels if lb not in update.labels_remove] + [lb for lb in update.labels_add if lb not in issue.labels]
        issue.fields.update(update.fields)

    async def assign_issue(self, key: str, assignee: str | None) -> None:
        issue = _find(self.s.issues, key.upper(), "issue")
        issue.assignee = next((u for u in self.s.users if assignee in (u.account_id, u.email, u.display_name)), None) if assignee else None

    async def transition_issue(self, key: str, transition: str, *, fields: dict[str, Any] | None = None,
                               comment: BodyInput | str | None = None) -> None:
        issue = _find(self.s.issues, key.upper(), "issue")
        t = next((x for x in self.s.transitions.get(key.upper(), []) if transition in (x.id, x.name) or x.name.lower() == transition.lower()), None)
        if t is None:
            raise NotFoundError(f"transition {transition!r} is not available for {key}", hint="Call list_transitions first.")
        issue.status = t.to_status
        if comment:
            await self.add_comment(key, comment)

    async def add_comment(self, key: str, body: BodyInput | str, *, visibility: dict[str, str] | None = None) -> Comment:
        _find(self.s.issues, key.upper(), "issue")
        c = Comment(id=f"c{next(self.s.counter)}", author=self.s.me, body_markdown=_body_text(body), visibility=visibility)
        self.s.comments.setdefault(key.upper(), []).append(c)
        return c

    async def edit_comment(self, key: str, comment_id: str, body: BodyInput | str) -> Comment:
        c = next((x for x in self.s.comments.get(key.upper(), []) if x.id == comment_id), None)
        if c is None:
            raise NotFoundError(f"comment {comment_id} not found")
        c.body_markdown = _body_text(body)
        return c

    async def add_worklog(self, key: str, time_spent: str, *, started: str | None = None, comment: BodyInput | str | None = None) -> Worklog:
        w = Worklog(id=f"w{next(self.s.counter)}", author=self.s.me, time_spent=time_spent, comment_markdown=_body_text(comment) or None)
        self.s.worklogs.setdefault(key.upper(), []).append(w)
        return w

    async def create_link(self, link_type: str, inward_key: str, outward_key: str) -> None:
        _find(self.s.issues, inward_key.upper(), "issue")
        _find(self.s.issues, outward_key.upper(), "issue")
        self.s.issues[inward_key.upper()].fields.setdefault("issuelinks", []).append(
            {"id": f"l{next(self.s.counter)}", "type": {"name": link_type, "outward": link_type},
             "outwardIssue": {"key": outward_key.upper(), "fields": {}}})

    async def create_remote_link(self, key: str, url: str, title: str) -> RemoteLink:
        r = RemoteLink(id=str(next(self.s.counter)), url=url, title=title)
        self.s.remote_links.setdefault(key.upper(), []).append(r)
        return r

    async def upload_attachment(self, key: str, file: Path | bytes, *, filename: str | None = None) -> list[Attachment]:
        data = file if isinstance(file, bytes) else file.read_bytes()
        aid = f"att{next(self.s.counter)}"
        self.s.attachment_bytes[aid] = data
        meta = {"id": aid, "filename": filename or (file.name if isinstance(file, Path) else "file"), "size": len(data)}
        _find(self.s.issues, key.upper(), "issue").fields.setdefault("attachment", []).append(meta)
        return [n.attachment(meta)]

    async def watch_issue(self, key: str, *, watch: bool = True, account_id: str | None = None) -> None:
        w = self.s.watchers.setdefault(key.upper(), [])
        target = account_id or self.s.me.account_id
        if watch:
            user = next((u for u in self.s.users if u.account_id == target), self.s.me)
            if user.account_id not in {x.account_id for x in w}:
                w.append(user)
        else:
            self.s.watchers[key.upper()] = [u for u in w if u.account_id != target]

    async def set_property(self, key: str, property_key: str, value: Any) -> None:
        self.s.properties.setdefault(key.upper(), {})[property_key] = value

    async def convert_hierarchy(self, key: str, *, new_issue_type: str, new_parent: str | None = None) -> None:
        issue = _find(self.s.issues, key.upper(), "issue")
        issue.issue_type, issue.parent_key = new_issue_type, new_parent

    # ---- deleter ----
    async def delete_issue(self, key: str, *, delete_subtasks: bool = False) -> None:
        _find(self.s.issues, key.upper(), "issue")
        del self.s.issues[key.upper()]

    async def delete_comment(self, key: str, comment_id: str) -> None:
        items = self.s.comments.get(key.upper(), [])
        if not any(c.id == comment_id for c in items):
            raise NotFoundError(f"comment {comment_id} not found")
        self.s.comments[key.upper()] = [c for c in items if c.id != comment_id]

    async def delete_attachment(self, attachment_id: str) -> None:
        _find(self.s.attachment_bytes, attachment_id, "attachment")
        del self.s.attachment_bytes[attachment_id]
        for issue in self.s.issues.values():
            issue.fields["attachment"] = [a for a in issue.fields.get("attachment", []) if str(a["id"]) != attachment_id]

    # ---- projects / metadata / users ----
    async def list_projects(self, *, query: str | None = None, limit: int = 50, cursor: str | None = None) -> Page[Project]:
        items = [p for p in self.s.projects if not query or query.lower() in (p.name or "").lower() or query.lower() in p.key.lower()]
        return self.page(items, limit, cursor)

    async def get_project(self, key: str) -> Project:
        return _find({p.key: p for p in self.s.projects}, key.upper(), "project")

    async def list_components(self, project: str) -> list[Component]:
        return list(self.s.components.get(project.upper(), []))

    async def list_versions(self, project: str) -> list[Version]:
        return list(self.s.versions.get(project.upper(), []))

    async def manage_version(self, project: str, change: VersionChange) -> Version:
        from ..core.errors import InvalidRequestError

        versions = self.s.versions.setdefault(project.upper(), [])
        if change.action == "create":
            v = Version(id=f"v{next(self.s.counter)}", name=change.name or "")
            versions.append(v)
            return v
        if change.action not in ("update", "release", "unrelease", "archive", "delete") or not change.version_id:
            raise InvalidRequestError(f"{change.action} needs a valid action and version_id", hint="Get ids from list_versions.")
        target = next((x for x in versions if x.id == change.version_id), None)
        if target is None:
            raise NotFoundError(f"version {change.version_id} not found")
        if change.action == "delete":
            versions.remove(target)
            return target
        target.name = change.name or target.name
        target.released = {"release": True, "unrelease": False}.get(change.action, target.released)
        target.archived = target.archived or change.action == "archive"
        return target

    async def list_fields(self) -> list[Field]:
        return list(self.s.fields)

    async def list_issue_types(self, project: str | None = None) -> list[IssueType]:
        return list(self.s.issue_types)

    async def get_create_fields(self, project: str, issue_type: str) -> list[Field]:
        t = next((x for x in self.s.issue_types if issue_type in (x.id, x.name) or x.name.lower() == issue_type.lower()), None)
        if t is None:
            raise NotFoundError(f"issue type {issue_type!r} not found in project {project}")
        raw = self.s.create_fields.get(f"{project.upper()}/{t.id}", [])
        return [n.jfield({"id": f["fieldId"], "name": f["name"], "schema": f.get("schema"), "required": f.get("required"),
                          "custom": f["fieldId"].startswith("customfield_")}) for f in raw]

    async def list_statuses(self, project: str | None = None) -> list[Status]:
        return list(self.s.statuses)

    async def list_priorities(self) -> list[str]:
        return list(self.s.priorities)

    async def list_link_types(self) -> list[str]:
        return list(self.s.link_types)

    async def list_filters(self, *, query: str | None = None, limit: int = 50, cursor: str | None = None) -> Page[Filter]:
        return self.page([f for f in self.s.filters if not query or query.lower() in f.name.lower()], limit, cursor)

    async def list_dashboards(self, *, limit: int = 50, cursor: str | None = None) -> Page[Dashboard]:
        return self.page(self.s.dashboards, limit, cursor)

    async def server_info(self) -> ServerInfo:
        return self.s.server_info

    async def myself(self) -> UserRef:
        return self.s.me

    async def get_user(self, account_id: str) -> UserRef:
        return _find({u.account_id or "": u for u in self.s.users}, account_id, "user")

    async def search_users(self, query: str, *, limit: int = 20) -> list[UserRef]:
        q = query.lower()
        return [u for u in self.s.users if q in (u.display_name or "").lower() or q in (u.email or "").lower()][:limit]

    async def lookup_account_id(self, query: str) -> str:
        from ..core.errors import AmbiguousReferenceError

        users = await self.search_users(query)
        exact = [u for u in users if query.lower() in ((u.email or "").lower(), (u.display_name or "").lower())]
        pool = exact or users
        if not pool:
            raise NotFoundError(f"no user matches {query!r}")
        if len(pool) > 1:
            raise AmbiguousReferenceError(f"{len(pool)} users match {query!r}", hint="Use the email or accountId.")
        return pool[0].account_id or ""

    async def assignable_users(self, *, issue_key: str | None = None, project: str | None = None, query: str | None = None) -> list[UserRef]:
        from ..core.errors import InvalidRequestError

        if not (issue_key or project):
            raise InvalidRequestError("assignable_users needs issue_key or project", hint="Pass one of them.")
        return await self.search_users(query or "")

    # ---- JQL tools ----
    async def parse_jql(self, queries: Sequence[str], *, validation: str = "strict") -> list[JqlParseResult]:
        return [JqlParseResult(query=q, valid="BROKEN" not in q, errors=["Error in the JQL Query"] if "BROKEN" in q else []) for q in queries]

    async def sanitize_jql(self, queries: Sequence[str]) -> list[str]:
        return [q.replace("ann", "acc-ann") for q in queries]

    async def autocomplete(self, *, field_name: str | None = None, field_value: str | None = None) -> dict[str, Any]:
        return {"results": [{"value": "Done", "displayName": "Done"}]} if field_name else {"visibleFieldNames": [{"value": "status"}]}

    async def match_jql(self, issue_ids: Sequence[int], jqls: Sequence[str]) -> dict[str, list[int]]:
        return {j: list(issue_ids) for j in jqls}

    async def evaluate_expression(self, expression: str, context: dict[str, Any]) -> Any:
        return 2 if expression.strip() == "1 + 1" else None

    # ---- admin ----
    async def create_project(self, key: str, name: str, *, template: str, lead: str) -> Project:
        p = Project(id=str(next(self.s.counter)), key=key, name=name)
        self.s.projects.append(p)
        return p

    async def update_project(self, key: str, **changes: Any) -> Project:
        p = await self.get_project(key)
        for k, v in changes.items():
            setattr(p, k, v)
        return p

    async def list_workflows(self, *, project: str | None = None) -> list[Workflow]:
        return [Workflow(id="wf1", name="Software workflow", statuses=[s.name for s in self.s.statuses])]

    async def get_workflow_editor_link(self, workflow_id: str) -> str:
        return f"https://example.atlassian.net/jira/settings/issues/workflows/{workflow_id}"

    async def get_screen(self, screen_id: str) -> Screen:
        return Screen(id=screen_id, name="Default screen")

    async def update_screen(self, screen_id: str, **changes: Any) -> Screen:
        return Screen(id=screen_id, name=str(changes.get("name", "Default screen")))


class FakeAgile(_Base):
    async def list_boards(self, *, project: str | None = None, name: str | None = None, limit: int = 50, cursor: str | None = None) -> Page[Board]:
        items = [b for b in self.s.boards if (not project or b.project_key == project) and (not name or name.lower() in b.name.lower())]
        return self.page(items, limit, cursor)

    async def get_board(self, board_id: int) -> Board:
        return _find({str(b.id): b for b in self.s.boards}, str(board_id), "board")

    async def get_board_config(self, board_id: int) -> BoardConfig:
        return _find(self.s.board_configs, str(board_id), "board configuration")

    async def list_board_issues(self, board_id: int, *, jql: str | None = None, limit: int = 50, cursor: str | None = None) -> Page[Issue]:
        return self.page(_find(self.s.board_issues, str(board_id), "board"), limit, cursor)

    async def list_sprints(self, board_id: int, *, states: Sequence[str] = ("active", "future"), limit: int = 50, cursor: str | None = None) -> Page[Sprint]:
        return self.page([x for x in _find(self.s.sprints, str(board_id), "board") if x.state in states], limit, cursor)

    async def get_sprint(self, sprint_id: int) -> Sprint:
        return _find({str(x.id): x for v in self.s.sprints.values() for x in v}, str(sprint_id), "sprint")

    async def list_sprint_issues(self, sprint_id: int, *, jql: str | None = None, limit: int = 50, cursor: str | None = None) -> Page[Issue]:
        return self.page(_find(self.s.sprint_issues, str(sprint_id), "sprint"), limit, cursor)

    async def list_backlog(self, board_id: int, *, limit: int = 50, cursor: str | None = None) -> Page[Issue]:
        return self.page(_find(self.s.backlog, str(board_id), "board"), limit, cursor)

    async def list_epics(self, board_id: int, *, limit: int = 50, cursor: str | None = None) -> Page[Epic]:
        return self.page(_find(self.s.epics, str(board_id), "board"), limit, cursor)

    async def manage_sprint(self, change: SprintChange) -> Sprint:
        from ..core.errors import InvalidRequestError

        if change.action == "create" and (change.board_id is None or not change.name):
            raise InvalidRequestError("creating a sprint needs board_id and name", hint="Set SprintChange.board_id and .name.")
        if change.action != "create" and change.sprint_id is not None:
            for sprints in self.s.sprints.values():
                for existing in sprints:
                    if existing.id == change.sprint_id:
                        if change.action == "delete":
                            sprints.remove(existing)
                            return existing.model_copy(update={"state": "deleted"})
                        existing.state = {"start": "active", "close": "closed"}.get(change.action, existing.state)
                        existing.name = change.name or existing.name
                        return existing
        sid = change.sprint_id or next(self.s.counter)
        sp = Sprint(id=sid, name=change.name or f"Sprint {sid}", state={"start": "active", "close": "closed"}.get(change.action, "future"), goal=change.goal)
        if change.board_id is not None:
            self.s.sprints.setdefault(str(change.board_id), []).append(sp)
        return sp

    async def move_issues_to_sprint(self, sprint_id: int, issue_keys: Sequence[str]) -> None:
        self.s.sprint_issues.setdefault(str(sprint_id), []).extend(self.s.issues[k] for k in issue_keys if k in self.s.issues)

    async def move_issues_to_backlog(self, issue_keys: Sequence[str]) -> None:
        return None

    async def rank_issues(self, issue_keys: Sequence[str], *, before: str | None = None, after: str | None = None) -> None:
        from ..core.errors import InvalidRequestError

        if bool(before) == bool(after):
            raise InvalidRequestError("rank needs exactly one of before / after", hint="Pass the key to rank before or after.")

    async def create_board(self, name: str, *, filter_id: int, board_type: str = "scrum") -> Board:
        b = Board(id=next(self.s.counter), name=name, type=board_type)
        self.s.boards.append(b)
        return b


class FakeJsm(_Base):
    async def info(self) -> JsmInfo:
        return self.s.jsm_info

    async def list_service_desks(self, *, limit: int = 50, cursor: str | None = None) -> Page[ServiceDesk]:
        return self.page(self.s.service_desks, limit, cursor)

    async def list_request_types(self, service_desk_id: str, *, limit: int = 50, cursor: str | None = None) -> Page[RequestType]:
        return self.page(_find(self.s.request_types, service_desk_id, "service desk"), limit, cursor)

    async def list_requests(self, *, service_desk_id: str | None = None, status: str | None = None, search: str | None = None,
                            limit: int = 50, cursor: str | None = None) -> Page[CustomerRequest]:
        items = [r for r in self.s.requests if (not service_desk_id or r.service_desk_id == service_desk_id)
                 and (not search or search.lower() in (r.summary or "").lower())]
        return self.page(items, limit, cursor)

    async def get_request(self, key: str) -> CustomerRequest:
        return _find({r.issue_key: r for r in self.s.requests}, key.upper(), "request")

    async def list_request_comments(self, key: str, *, public_only: bool = False, limit: int = 50, cursor: str | None = None) -> Page[RequestComment]:
        items = [c for c in _find(self.s.request_comments, key.upper(), "request") if c.public or not public_only]
        return self.page(items, limit, cursor)

    async def get_request_sla(self, key: str) -> list[SlaInfo]:
        return list(_find(self.s.sla, key.upper(), "request"))

    async def search_knowledge_base(self, query: str, *, service_desk_id: str | None = None, limit: int = 20) -> list[KbArticle]:
        return [a for a in self.s.kb if query.lower() in a.title.lower() or query.lower() in (a.excerpt or "").lower()][:limit]

    async def list_organizations(self, *, limit: int = 50, cursor: str | None = None) -> Page[Organization]:
        return self.page(self.s.organizations, limit, cursor)

    async def list_customers(self, service_desk_id: str, *, limit: int = 50, cursor: str | None = None) -> Page[UserRef]:
        return self.page(_find(self.s.customers, service_desk_id, "service desk"), limit, cursor)

    async def create_request(self, service_desk_id: str, request_type_id: str, fields: dict[str, Any], *, on_behalf_of: str | None = None) -> CustomerRequest:
        key = f"OPS-{next(self.s.counter)}"
        r = CustomerRequest(issue_id=str(next(self.s.counter)), issue_key=key, request_type=request_type_id, service_desk_id=service_desk_id,
                            summary=str(fields.get("summary", "")), fields=dict(fields))
        self.s.requests.append(r)
        return r

    async def add_request_comment(self, key: str, body: BodyInput | str, *, public: bool) -> RequestComment:
        c = RequestComment(id=f"rc{next(self.s.counter)}", author=self.s.me, body=_body_text(body), public=public)
        self.s.request_comments.setdefault(key.upper(), []).append(c)
        return c

    async def transition_request(self, key: str, transition: str, *, comment: BodyInput | str | None = None) -> None:
        r = await self.get_request(key)
        r.status = transition

    async def answer_approval(self, key: str, approval_id: str, *, approve: bool) -> None:
        await self.get_request(key)

    async def add_customers(self, service_desk_id: str, account_ids: list[str]) -> None:
        self.s.customers.setdefault(service_desk_id, []).extend(UserRef(account_id=a) for a in account_ids)


class FakeConfluence(_Base):
    """Confluence reader, searcher, spaces, templates and all writers/deleters."""

    def _get(self, content_id: str) -> Content:
        return _find(self.s.pages, content_id, "content").model_copy(deep=True)

    async def get_content(self, content_id: str, *, type: ContentType = ContentType.PAGE, body_format: BodyFormat = "markdown",
                          version: int | None = None) -> Content:
        c = self._get(content_id)
        if version is not None and version != c.version:
            html = self.s.page_versions.get(content_id, {}).get(str(version))
            if html is None:
                raise NotFoundError(f"version {version} of {content_id} not found")
            from ..formats.storage import storage_to_markdown

            c.body_markdown, c.body_storage, c.version = storage_to_markdown(html), html, version
        if body_format == "none":
            c.body_markdown = c.body_storage = None
        return c

    async def list_content(self, *, space_id: str | None = None, type: ContentType = ContentType.PAGE, status: str = "current",
                           title: str | None = None, limit: int = 25, cursor: str | None = None) -> Page[Content]:
        items = [c for c in self.s.pages.values() if (not space_id or c.space_id == space_id) and (not title or c.title == title)]
        return self.page(items, limit, cursor)

    async def list_children(self, content_id: str, *, type: ContentType = ContentType.PAGE, limit: int = 25, cursor: str | None = None) -> Page[Content]:
        _find(self.s.pages, content_id, "content")
        return self.page([self.s.pages[i] for i in self.s.children.get(content_id, [])], limit, cursor)

    async def list_ancestors(self, content_id: str, *, type: ContentType = ContentType.PAGE) -> list[Content]:
        _find(self.s.pages, content_id, "content")
        return [self.s.pages[i] for i in self.s.ancestors.get(content_id, [])]

    async def list_descendants(self, content_id: str, *, depth: int | None = None, limit: int = 50, cursor: str | None = None) -> Page[Content]:
        _find(self.s.pages, content_id, "content")
        return self.page([self.s.pages[i] for i in self.s.descendants.get(content_id, [])], limit, cursor)

    async def list_versions(self, content_id: str, *, limit: int = 25, cursor: str | None = None) -> Page[ContentVersion]:
        return self.page(_find(self.s.content_versions, content_id, "content"), limit, cursor)

    async def diff_versions(self, content_id: str, from_version: int, to_version: int) -> ContentDiff:
        a = await self.get_content(content_id, version=from_version)
        b = await self.get_content(content_id, version=to_version)
        diff = "\n".join(difflib.unified_diff((a.body_markdown or "").splitlines(), (b.body_markdown or "").splitlines(),
                                              f"v{from_version}", f"v{to_version}", lineterm=""))
        return ContentDiff(content_id=content_id, from_version=from_version, to_version=to_version, unified=diff)

    async def list_labels(self, content_id: str) -> list[Label]:
        _find(self.s.pages, content_id, "content")
        return list(self.s.labels.get(content_id, []))

    async def get_properties(self, content_id: str) -> dict[str, Any]:
        _find(self.s.pages, content_id, "content")
        return dict(self.s.content_properties.get(content_id, {}))

    async def list_comments(self, content_id: str, *, kind: Literal["footer", "inline", "all"] = "all", limit: int = 25,
                            cursor: str | None = None) -> Page[ConfluenceComment]:
        _find(self.s.pages, content_id, "content")
        items = []
        if kind in ("footer", "all"):
            items += self.s.footer.get(content_id, [])
        if kind in ("inline", "all"):
            items += self.s.inline.get(content_id, [])
        return self.page(items, limit, cursor)

    async def list_attachments(self, content_id: str, *, limit: int = 25, cursor: str | None = None) -> Page[CAttachment]:
        _find(self.s.pages, content_id, "content")
        return self.page(self.s.conf_attachments.get(content_id, []), limit, cursor)

    async def download_attachment(self, attachment_id: str, *, dest: Path | None = None, max_bytes: int | None = None,
                                  extract_text: bool = False) -> CAttachmentContent:
        data = _find(self.s.conf_attachment_bytes, attachment_id, "attachment")
        if max_bytes is not None and len(data) > max_bytes:
            raise ConflictError(f"attachment is {len(data)} bytes, above max_bytes={max_bytes}", status=413)
        meta = next((a for v in self.s.conf_attachments.values() for a in v if a.id == attachment_id), None)
        mime = meta.media_type if meta else None
        text = data.decode("utf-8", "replace") if extract_text and (mime or "").startswith("text/") else None
        path = None
        if dest is not None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            path = str(dest)
        return CAttachmentContent(attachment_id=attachment_id, filename=meta.title if meta else None, mime_type=mime, size=len(data),
                                  data=None if dest else data, path=path, text=text)

    async def get_permissions(self, content_id: str) -> Permissions:
        _find(self.s.pages, content_id, "content")
        return Permissions(operations=list(self.s.operations.get(content_id, [])))

    async def get_restrictions(self, content_id: str) -> Restrictions:
        _find(self.s.pages, content_id, "content")
        return self.s.restrictions.get(content_id, Restrictions())

    async def list_tasks(self, *, space_id: str | None = None, content_id: str | None = None,
                         status: Literal["complete", "incomplete"] | None = None, limit: int = 25, cursor: str | None = None) -> Page[Task]:
        items = [t for t in self.s.tasks if (not content_id or t.content_id == content_id) and (not status or t.status == status)]
        return self.page(items, limit, cursor)

    async def resolve_macros(self, content_id: str) -> Content:
        return self._get(content_id)

    async def search_cql(self, cql: str, *, limit: int = 25, cursor: str | None = None, excerpt: bool = True) -> Page[SearchHit]:
        from ..core.errors import InvalidRequestError

        if not cql.strip():
            raise InvalidRequestError("empty CQL query", hint='Example: space = "ENG" AND type = page.')
        return self.page(self.s.search, limit, cursor)

    async def list_spaces(self, *, keys: Sequence[str] = (), type: str | None = None, limit: int = 25, cursor: str | None = None) -> Page[Space]:
        return self.page([s for s in self.s.spaces if (not keys or s.key in keys) and (not type or s.type == type)], limit, cursor)

    async def get_space(self, key_or_id: str) -> Space:
        sp = next((s for s in self.s.spaces if key_or_id in (s.key, s.id)), None)
        if sp is None:
            raise NotFoundError(f"space {key_or_id!r} not found", hint="Space keys are case-sensitive; or you lack access.")
        return sp

    async def get_personal_space(self, account_id: str | None = None) -> Space:
        raise NotFoundError("personal space not found")

    async def get_space_instructions(self, key_or_id: str) -> str | None:
        raise UnsupportedOperationError("space instructions are not available through the REST API",
                                        operation="confluence.space.instructions.get")

    async def list_space_permissions(self, key_or_id: str, *, limit: int = 50, cursor: str | None = None) -> Page[dict[str, Any]]:
        await self.get_space(key_or_id)
        return Page(items=[])

    async def list_templates(self, *, space_key: str | None = None) -> list[Template]:
        return [t for t in self.s.templates if not space_key or t.space_key == space_key]

    async def get_template(self, template_id: str) -> Template:
        return _find({t.id: t for t in self.s.templates}, template_id, "template")

    # ---- writers ----
    async def create_content(self, draft: ContentDraft) -> Content:
        sp = await self.get_space(draft.space)
        cid = str(next(self.s.counter))
        c = Content(id=cid, type=draft.type, title=draft.title, space_id=sp.id, parent_id=draft.parent_id, version=1,
                    status=draft.status, body_markdown=draft.body)
        self.s.pages[cid] = c
        self.s.content_versions[cid] = [ContentVersion(number=1)]
        return c

    async def update_content(self, content_id: str, update: ContentUpdate) -> Content:
        c = _find(self.s.pages, content_id, "content")
        if update.version is not None and update.version != c.version:
            raise ConflictError("Page version changed. Re-fetch and retry with version = current + 1.", hint="Page version changed. Re-fetch with confluence_get_page "
                                                                                                 "and retry with version = current.", status=409)
        c.version = (c.version or 1) + 1
        c.title = update.title or c.title
        c.body_markdown = update.body if update.body is not None else c.body_markdown
        self.s.content_versions.setdefault(content_id, []).insert(0, ContentVersion(number=c.version, message=update.message))
        return c.model_copy(deep=True)

    async def move_content(self, content_id: str, *, target_parent_id: str | None = None, target_space: str | None = None, position: str = "append") -> Content:
        from ..core.errors import InvalidRequestError

        if not target_parent_id:
            raise InvalidRequestError("move needs target_parent_id", hint="Moving to a space root needs the space homepage id as target_parent_id.")
        c = _find(self.s.pages, content_id, "content")
        c.parent_id = target_parent_id
        return c

    async def copy_content(self, content_id: str, *, destination_parent_id: str, title: str | None = None) -> Content:
        src = _find(self.s.pages, content_id, "content")
        cid = str(next(self.s.counter))
        c = src.model_copy(update={"id": cid, "parent_id": destination_parent_id, "title": title or f"Copy of {src.title}", "version": 1})
        self.s.pages[cid] = c
        return c

    async def archive_content(self, content_ids: Sequence[str]) -> None:
        for i in content_ids:
            _find(self.s.pages, i, "content").status = "archived"

    async def unarchive_content(self, content_ids: Sequence[str]) -> None:
        for i in content_ids:
            _find(self.s.pages, i, "content").status = "current"

    async def restore_version(self, content_id: str, version: int, *, message: str | None = None) -> Content:
        old = await self.get_content(content_id, version=version)
        return await self.update_content(content_id, ContentUpdate(body=old.body_markdown, message=message))

    async def set_status(self, content_id: str, status: str) -> None:
        _find(self.s.pages, content_id, "content").status = status

    async def add_labels(self, content_id: str, labels: Sequence[str]) -> list[Label]:
        cur = self.s.labels.setdefault(content_id, [])
        cur += [Label(name=x) for x in labels if x not in {c.name for c in cur}]
        return list(cur)

    async def remove_label(self, content_id: str, label: str) -> None:
        self.s.labels[content_id] = [x for x in self.s.labels.get(content_id, []) if x.name != label]

    async def add_comment(self, content_id: str, body: BodyInput | str, *, parent_comment_id: str | None = None,
                          inline_anchor: dict[str, Any] | None = None) -> ConfluenceComment:
        c = ConfluenceComment(id=f"fc{next(self.s.counter)}", kind="inline" if inline_anchor else "footer", body_markdown=_body_text(body),
                              parent_id=parent_comment_id)
        (self.s.inline if inline_anchor else self.s.footer).setdefault(content_id, []).append(c)
        return c

    async def update_comment(self, comment_id: str, body: BodyInput | str) -> ConfluenceComment:
        for store in (self.s.footer, self.s.inline):
            for items in store.values():
                for c in items:
                    if c.id == comment_id:
                        c.body_markdown = _body_text(body)
                        return c
        raise NotFoundError(f"comment {comment_id} not found")

    async def resolve_comment(self, comment_id: str, *, resolved: bool = True) -> None:
        (await self.update_comment(comment_id, "")).resolved = resolved

    async def upload_attachment(self, content_id: str, file: Path | bytes, *, filename: str | None = None, comment: str | None = None) -> CAttachment:
        data = file if isinstance(file, bytes) else file.read_bytes()
        aid = f"cat{next(self.s.counter)}"
        self.s.conf_attachment_bytes[aid] = data
        a = CAttachment(id=aid, title=filename or "file", file_size=len(data), page_id=content_id)
        self.s.conf_attachments.setdefault(content_id, []).append(a)
        return a

    async def set_property(self, content_id: str, key: str, value: Any) -> None:
        self.s.content_properties.setdefault(content_id, {})[key] = value

    async def set_restrictions(self, content_id: str, restrictions: Restrictions) -> None:
        self.s.restrictions[content_id] = restrictions

    async def set_task_status(self, task_id: str, *, complete: bool) -> Task:
        t = _find({x.id: x for x in self.s.tasks}, task_id, "task")
        t.status = "complete" if complete else "incomplete"
        return t

    async def watch(self, *, content_id: str | None = None, space_key: str | None = None, label: str | None = None, watch: bool = True) -> None:
        from ..core.errors import InvalidRequestError

        if not (content_id or space_key or label):
            raise InvalidRequestError("watch needs content_id, space_key or label", hint="Pass exactly one.")

    async def star(self, *, content_id: str | None = None, space_key: str | None = None, star: bool = True) -> None:
        return None

    async def add_reaction(self, content_id: str, emoji: str) -> None:
        _find(self.s.pages, content_id, "content")

    async def create_space(self, key: str, name: str, *, description: str | None = None) -> Space:
        sp = Space(id=str(next(self.s.counter)), key=key, name=name, type="global", status="current")
        self.s.spaces.append(sp)
        return sp

    async def set_space_instructions(self, key_or_id: str, instructions: str) -> None:
        await self.get_space(key_or_id)

    async def add_permissions(self, content_id: str, grants: Sequence[dict[str, Any]]) -> None:
        from ..backends.rest_cloud.confluence_write import _apply

        _find(self.s.pages, content_id, "content")
        self.s.restrictions[content_id] = _apply(self.s.restrictions.get(content_id, Restrictions()), grants, remove=False)

    async def remove_permissions(self, content_id: str, grants: Sequence[dict[str, Any]]) -> None:
        from ..backends.rest_cloud.confluence_write import _apply

        _find(self.s.pages, content_id, "content")
        self.s.restrictions[content_id] = _apply(self.s.restrictions.get(content_id, Restrictions()), grants, remove=True)

    async def replace_permissions(self, content_id: str, grants: Sequence[dict[str, Any]]) -> None:
        from ..backends.rest_cloud.confluence_write import _apply

        _find(self.s.pages, content_id, "content")
        self.s.restrictions[content_id] = _apply(Restrictions(), grants, remove=False)

    async def set_public_link(self, content_id: str, *, enabled: bool) -> None:
        _find(self.s.pages, content_id, "content")

    async def delete_content(self, content_id: str, *, type: ContentType = ContentType.PAGE, purge: bool = False) -> None:
        _find(self.s.pages, content_id, "content")
        del self.s.pages[content_id]

    async def delete_comment(self, comment_id: str) -> None:
        for store in (self.s.footer, self.s.inline):
            for k, items in store.items():
                if any(c.id == comment_id for c in items):
                    store[k] = [c for c in items if c.id != comment_id]
                    return
        raise NotFoundError(f"comment {comment_id} not found")

    async def delete_attachment(self, attachment_id: str, *, purge: bool = False) -> None:
        _find(self.s.conf_attachment_bytes, attachment_id, "attachment")
        del self.s.conf_attachment_bytes[attachment_id]
        for k, items in self.s.conf_attachments.items():
            self.s.conf_attachments[k] = [a for a in items if a.id != attachment_id]


class FakeConfluenceSpecial(_Base):
    """MCP-only special content (infographics, MAUI apps)."""

    async def get_maui_app(self, app_id: str) -> dict[str, Any]:
        return {"id": app_id, "spec": "demo"}

    async def create_infographic(self, content_id: str, *, prompt: str) -> dict[str, Any]:
        _find(self.s.pages, content_id, "content")
        return {"id": f"inf{next(self.s.counter)}", "contentId": content_id, "prompt": prompt}

    async def edit_infographic(self, content_id: str, infographic_id: str, *, prompt: str) -> dict[str, Any]:
        return {"id": infographic_id, "prompt": prompt}

    async def create_maui_app(self, content_id: str, *, spec: str) -> dict[str, Any]:
        _find(self.s.pages, content_id, "content")
        return {"id": f"app{next(self.s.counter)}", "contentId": content_id, "spec": spec}

    async def edit_maui_app(self, app_id: str, *, spec: str) -> dict[str, Any]:
        return {"id": app_id, "spec": spec}


class FakeGraph(_Base):
    """Teamwork Graph, Rovo search and GraphQL."""

    async def get_context(self, ari_or_url: str, *, max_items: int = 50) -> GraphContext:
        return GraphContext(root=ari_or_url, related=[GraphObject(ari="ari:cloud:jira::issue/10001", title="Login fails")][:max_items])

    async def get_objects(self, aris_or_urls: Sequence[str]) -> list[GraphObject]:
        return [GraphObject(ari=a, title=a) for a in aris_or_urls]

    async def add_context(self, ari_or_url: str, context: Mapping[str, Any]) -> None:
        return None

    async def semantic_search(self, query: str, *, products: Sequence[str] = (), limit: int = 20) -> list[SemanticHit]:
        return [SemanticHit(title="Runbook", product="confluence", snippet=query, score=0.9)][:limit]

    async def execute(self, query: str, variables: Mapping[str, Any] | None = None, *, experimental_apis: Sequence[str] = (),
                      operation_name: str | None = None) -> dict[str, Any]:
        return {"data": {"echo": query}}


class FakeAnalytics(_Base):
    async def list_shares(self) -> list[str]:
        return ["share1"]

    async def list_tables(self, share: str | None = None) -> list[str]:
        return ["jira_issue", "confluence_page"]

    async def load_table(self, table: str, *, columns: Sequence[str] | None = None, limit: int | None = None,
                         as_: Literal["records", "pandas", "arrow"] = "records") -> Any:
        return [{"key": k} for k in self.s.issues][:limit]

    async def query(self, sql: str, *, tables: Sequence[str]) -> list[dict[str, Any]]:
        return [{"n": len(self.s.issues)}]


class FakeExports(_Base):
    async def export_issues_csv(self, jql: str, *, fields: Sequence[str] | None = None, dest: Path) -> Path:
        dest.write_text("key,summary\n" + "\n".join(f"{i.key},{i.summary}" for i in self.s.issues.values()), encoding="utf-8")
        return dest

    async def export_content(self, content_id: str, *, format: Literal["pdf", "word", "html", "markdown"], dest: Path) -> Path:
        c = _find(self.s.pages, content_id, "content")
        dest.write_text(c.body_markdown or "", encoding="utf-8")
        return dest

    async def export_space(self, space_key: str, *, format: Literal["pdf", "html", "xml", "csv"], dest: Path) -> Path:
        dest.write_text(space_key, encoding="utf-8")
        return dest


class FakeAudit(_Base):
    def __init__(self, store: FakeStore) -> None:
        super().__init__(store)
        self.events = [AuditEvent(id=f"e{i}", action="user_created", occurred_at=datetime(2026, 9, 1, tzinfo=UTC)) for i in range(3)]

    async def list_events(self, *, start: datetime | None = None, end: datetime | None = None, action: str | None = None, query: str | None = None,
                          cursor: str | None = None, limit: int = 200) -> Page[AuditEvent]:
        return self.page(self.events, limit, cursor)

    async def get_event(self, event_id: str) -> AuditEvent:
        return _find({e.id: e for e in self.events}, event_id, "event")

    async def list_event_actions(self) -> list[str]:
        return ["user_created"]

    async def poll_stream(self, *, cursor: str | None = None, start: datetime | None = None, limit: int = 500,
                          sort_order: Literal["asc", "desc"] = "asc") -> AuditEventPage:
        page = self.page(self.events, limit, cursor)
        return AuditEventPage(events=page.items, next_cursor=page.next_cursor or Cursor(self.s.kind, "fake", {"i": len(self.events)}).encode())


class FakeAutomation(_Base):
    def __init__(self, store: FakeStore) -> None:
        super().__init__(store)
        self.triggered: list[dict[str, Any]] = []

    async def trigger_incoming_webhook(self, webhook_url: str, *, secret: str | None = None, issue_keys: Sequence[str] = (),
                                       data: Mapping[str, Any] | None = None) -> None:
        self.triggered.append({"url": webhook_url, "issues": list(issue_keys), "data": dict(data or {})})


class FakeWebhooks(_Base):
    def __init__(self, store: FakeStore) -> None:
        super().__init__(store)
        self.registrations: list[WebhookRegistration] = []

    async def list_webhooks(self) -> list[WebhookRegistration]:
        return list(self.registrations)

    async def register(self, url: str, subscriptions: Sequence[WebhookSubscription], *, secret: str | None = None) -> list[WebhookRegistration]:
        out = [WebhookRegistration(id=str(next(self.s.counter)), url=url, events=s.events, jql_filter=s.jql_filter) for s in subscriptions]
        self.registrations += out
        return out

    async def delete(self, webhook_ids: Sequence[str]) -> None:
        self.registrations = [r for r in self.registrations if r.id not in webhook_ids]

    async def refresh(self, webhook_ids: Sequence[str]) -> datetime | None:
        return datetime(2026, 12, 1, tzinfo=UTC)

    async def list_failed(self, *, after: datetime | None = None) -> list[FailedWebhook]:
        return []


class FakeIdentity(_Base):
    async def whoami(self) -> Identity:
        return Identity(account_id=self.s.me.account_id, display_name=self.s.me.display_name, email=self.s.me.email, site="example.atlassian.net",
                        auth_scheme="fake", backend=self.s.kind.value)

    async def accessible_resources(self) -> list[AccessibleResource]:
        return [AccessibleResource(id="cloud-1", name="Example", url="https://example.atlassian.net")]


class FakeRaw(_Base):
    async def request(self, method: str, path: str, *, product: Literal["jira", "confluence", "jsm", "agile", "admin"],
                      params: Mapping[str, Any] | None = None, json: Any = None, headers: Mapping[str, str] | None = None) -> RawResponse:
        if path == "/myself":
            return RawResponse(status=200, json_body={"accountId": self.s.me.account_id})
        raise UnsupportedOperationError(f"FakeBackend raw request {method} {path} is not scripted")

    def paginate(self, method: str, path: str, *, product: str, strategy: str, params: Mapping[str, Any] | None = None, json: Any = None) -> Any:
        async def gen() -> Any:
            for i in self.s.issues.values():
                yield {"key": i.key}
        return gen()


class FakeBackend:
    """In-memory backend. ``kind`` is configurable so router tests can register several fakes."""

    deployments: ClassVar[frozenset[Deployment]] = frozenset({Deployment.CLOUD, Deployment.DATA_CENTER})

    def __init__(self, kind: BackendKind = BackendKind.REST, *, raw: dict[str, Any] | None = None,
                 operations: frozenset[str] | None = None) -> None:
        self.kind = kind
        self.store = FakeStore(raw or scenario(), kind)
        s = self.store
        self.jira, self.agile, self.jsm = FakeJira(s), FakeAgile(s), FakeJsm(s)
        self.special = FakeConfluenceSpecial(s)
        self.confluence, self.graph, self.analytics, self.exports = FakeConfluence(s), FakeGraph(s), FakeAnalytics(s), FakeExports(s)
        self.audit, self.automation, self.webhooks = FakeAudit(s), FakeAutomation(s), FakeWebhooks(s)
        self.identity, self.raw = FakeIdentity(s), FakeRaw(s)
        self._parts: tuple[object, ...] = (self.jira, self.agile, self.jsm, self.confluence, self.special, self.graph, self.analytics, self.exports,
                                           self.audit, self.automation, self.webhooks, self.identity, self.raw)
        self._ops = operations if operations is not None else SUPPORTED | frozenset(ALL_OP_TOOLS)
        self.calls: list[str] = []

    def implementation_for(self, protocol: type) -> object | None:
        return next((p for p in self._parts if isinstance(p, protocol)), None)

    def supported_operations(self) -> frozenset[str]:
        return self._ops

    async def health(self) -> BackendHealth:
        return BackendHealth("ready", "in-memory fake")

    async def aclose(self) -> None:
        return None
