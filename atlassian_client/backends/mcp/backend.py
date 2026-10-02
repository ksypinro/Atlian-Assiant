"""McpBackend: Atlassian Remote MCP Server v2 as a read/search backend (design section 10.3).

Composed of one component per API family, like RestBackend. Operations the MCP catalog does not offer raise
``UnsupportedOperationError`` BEFORE anything is sent, so the router can fall back to another backend.
Argument names / result shapes are best-effort (design section 22 item 8) and isolated here.
"""
from __future__ import annotations

import base64
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, ClassVar, Literal, TypeVar

from ...capabilities import BackendKind, Deployment
from ...config import AtlassianConfig
from ...core.errors import (
    AmbiguousReferenceError,
    AtlassianError,
    BackendUnavailableError,
    ConfigurationError,
    NotFoundError,
    PermissionDeniedError,
    ServerError,
    UnsupportedOperationError,
    error_for_status,
)
from ...core.pagination import Cursor
from ...core.services import CoreServices
from ...factory import Availability
from ...formats.storage import storage_to_markdown
from ...models.common import Page, UserRef
from ...models.confluence import Attachment as CAttachment
from ...models.confluence import AttachmentContent as CAttachmentContent
from ...models.confluence import (
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
from ...models.graph import GraphContext, GraphObject, SemanticHit
from ...models.identity import AccessibleResource, Identity
from ...models.jira import (
    Attachment,
    AttachmentContent,
    Board,
    BoardConfig,
    ChangelogEntry,
    Comment,
    Component,
    Dashboard,
    Epic,
    Field,
    Filter,
    Issue,
    IssueLink,
    IssueType,
    Project,
    ProjectRef,
    RemoteLink,
    ServerInfo,
    Sprint,
    Status,
    Transition,
    Version,
    Worklog,
)
from ...models.jsm import (
    CustomerRequest,
    JsmInfo,
    KbArticle,
    Organization,
    RequestComment,
    RequestType,
    ServiceDesk,
    SlaInfo,
)
from ...protocols.base import BackendHealth
from ...protocols.confluence import BodyFormat, ContentType
from ..rest_cloud import normalize as n
from .session import McpToolResult, McpTransport, SdkMcpTransport

T = TypeVar("T")

# operation id -> MCP tool that serves it (design 10.3). Only ops with a tool are claimed.
OP_TOOLS: dict[str, str] = {
    "jira.issue.get": "getJiraIssue", "jira.issue.search": "searchJiraIssuesUsingJql",
    "jira.issue.comments.list": "listJiraIssueComments", "jira.issue.changelog.list": "listJiraIssueChangelogs",
    "jira.issue.worklogs.list": "listJiraIssueWorklogs", "jira.issue.transitions.list": "listJiraIssueTransitions",
    "jira.issue.links.list": "getJiraIssue", "jira.issue.attachments.list": "getJiraIssue",
    "jira.issue.remote_links.list": "listJiraIssueRemoteIssueLinks", "jira.issue.attachment.download": "downloadJiraIssueAttachment",
    "jira.issue.property.get": "getJiraEntityProperty",
    "jira.project.list": "listJiraProjects", "jira.project.get": "listJiraProjects", "jira.project.components.list": "listJiraProjectComponents",
    "jira.project.versions.list": "getJiraProjectVersions",
    "jira.meta.issue_types.list": "listJiraProjectIssueTypesMetadata", "jira.meta.create_fields": "getJiraIssueTypeMetaWithFields",
    "jira.meta.statuses.list": "listJiraStatuses", "jira.meta.link_types.list": "listJiraIssueLinkTypes",
    "jira.filter.list": "listJiraFilters", "jira.dashboard.list": "listJiraDashboards",
    "jira.user.myself": "getJiraCurrentUser", "jira.user.get": "getJiraUser", "jira.user.search": "lookupJiraAccountId",
    "jira.user.lookup_account_id": "lookupJiraAccountId", "jira.user.assignable": "listJiraIssueAssignableUsers",
    "jira.agile.board.list": "listJiraBoards", "jira.agile.board.config": "getJiraBoardConfig", "jira.agile.board.issues": "getJiraBoardIssueData",
    "jira.agile.sprint.list": "listJiraBoardSprints", "jira.agile.sprint.issues": "getJiraBoardSprintData",
    "jsm.request.get": "getJiraIssue",
    "confluence.content.get": "getConfluenceContent", "confluence.content.list": "listConfluenceContent",
    "confluence.content.children": "listConfluenceContent", "confluence.content.versions": "listConfluenceContentVersions",
    "confluence.content.diff": "diffConfluenceContentVersions", "confluence.content.macros.resolve": "resolveConfluenceContentMacros",
    "confluence.label.list": "getConfluenceContent", "confluence.comment.list": "listConfluenceComments",
    "confluence.attachment.list": "listConfluenceAttachments", "confluence.attachment.download": "downloadConfluenceAttachment",
    "confluence.permission.get": "getConfluenceContentPermissions", "confluence.permission.restrictions.get": "getConfluenceContentRestrictionState",
    "confluence.task.list": "listConfluenceTasks", "confluence.template.list": "listConfluenceTemplates",
    "confluence.template.get": "getConfluenceTemplate", "confluence.reaction.list": "getConfluenceReactions",
    "confluence.space.list": "listConfluenceSpaces", "confluence.space.get": "getConfluenceSpace",
    "confluence.space.personal": "getConfluencePersonalSpace", "confluence.space.instructions.get": "getConfluenceSpaceInstructions",
    "confluence.search.cql": "searchConfluence",
    "graph.context": "getTeamworkGraphContext", "graph.object.get": "getTeamworkGraphObject", "graph.search.semantic": "search",
    "identity.whoami": "atlassianUserInfo", "identity.accessible_resources": "getAccessibleAtlassianResources",
}

def _all_op_tools() -> dict[str, str]:
    from .write import WRITE_OP_TOOLS

    return {**OP_TOOLS, **WRITE_OP_TOOLS}


_STATUS_RE = re.compile(r"\b(400|401|403|404|409|422|429)\b")


def unsupported(op: str, why: str = "the Atlassian MCP v2 catalog has no tool for it") -> UnsupportedOperationError:
    return UnsupportedOperationError(f"{op} is not available through MCP: {why}", operation=op,
                                     hint="Another backend (REST) can serve it; the router falls back automatically.")


ALL_OP_TOOLS: dict[str, str] = {}


class McpClient:
    """Shared plumbing: tool availability, cloudId binding, error mapping, paging."""

    kind = BackendKind.MCP

    def __init__(self, transport: McpTransport, config: AtlassianConfig) -> None:
        self.transport, self.config = transport, config
        self._tools: set[str] | None = None
        self._cloud_id: str | None = config.cloud_id

    # ---- session ----
    async def tools(self) -> set[str]:
        if self._tools is None:
            self._tools = set(await self.transport.list_tools())
        return self._tools

    async def cloud_id(self) -> str:
        if self._cloud_id:
            return self._cloud_id
        data = await self.raw("getAccessibleAtlassianResources", {}, op="identity.accessible_resources", needs_cloud=False)
        sites = data if isinstance(data, list) else data.get("resources", data.get("sites", []))
        site = (self.config.site or "").lower()
        match = next((s for s in sites if site and site in str(s.get("url", "")).lower()), sites[0] if len(sites) == 1 else None)
        if match is None:
            raise ConfigurationError("could not determine the cloudId for the configured site",
                                     hint="Set ATLASSIAN_CLOUD_ID or ATLASSIAN_SITE; accessible: " + ", ".join(str(s.get("url")) for s in sites))
        self._cloud_id = str(match.get("id") or match.get("cloudId"))
        return self._cloud_id

    async def raw(self, tool: str, args: dict[str, Any], *, op: str, needs_cloud: bool = True) -> Any:
        """Call one tool and return its payload, mapping failures to AtlassianError. Raises Unsupported if the tool is absent."""
        live = await self.tools()
        if tool not in live:
            raise unsupported(op, f"tool {tool} is not exposed by this MCP session (its permission group may be disabled)")
        if needs_cloud:
            args = {"cloudId": await self.cloud_id(), **args}
        args = {k: v for k, v in args.items() if v is not None}
        result = await self.transport.call_tool(tool, args)
        if result.is_error:
            raise self._error(result, op)
        return result.payload()

    def _error(self, result: McpToolResult, op: str) -> AtlassianError:
        text = result.text or str(result.structured or "tool call failed")
        m = _STATUS_RE.search(text)
        if m:
            err = error_for_status(int(m.group(1)), text[:300], operation=op, backend="mcp")
        elif "not found" in text.lower():
            err = NotFoundError(text[:300], hint="Not found OR you lack permission.", operation=op, backend="mcp")
        elif "permission" in text.lower() or "forbidden" in text.lower() or "not allowed" in text.lower():
            err = PermissionDeniedError(text[:300], hint="The MCP permission group may be disabled by an org admin, or scopes are missing.",
                                        operation=op, backend="mcp")
        else:
            err = ServerError(text[:300], hint="The MCP tool failed; retry or use another backend.", operation=op, backend="mcp")
        return err

    # ---- paging ----
    async def offset_page(self, tool: str, args: dict[str, Any], items_key: str, mapper: Callable[[Any], T], *, op: str, limit: int,
                          cursor: str | None, extra_start: str = "startAt", extra_limit: str = "maxResults") -> Page[T]:
        start = int(Cursor.decode(cursor).state["start"]) if cursor else 0
        body = await self.raw(tool, {**args, extra_start: start, extra_limit: limit}, op=op)
        items = body if isinstance(body, list) else list(body.get(items_key, []))
        total = body.get("total") if isinstance(body, dict) else None
        is_last = body.get("isLast") if isinstance(body, dict) else len(items) < limit
        done = is_last is True or not items or (total is not None and start + len(items) >= int(total))
        nxt = None if done else Cursor(self.kind, "offset", {"start": start + len(items)}).encode()
        return Page(items=[mapper(i) for i in items], next_cursor=nxt, total=int(total) if total is not None else None)

    async def token_page(self, tool: str, args: dict[str, Any], items_key: str, mapper: Callable[[Any], T], *, op: str, limit: int,
                         cursor: str | None, token_arg: str = "nextPageToken", token_key: str = "nextPageToken") -> Page[T]:
        token = Cursor.decode(cursor).state["token"] if cursor else None
        body = await self.raw(tool, {**args, "maxResults": limit, token_arg: token}, op=op)
        nxt = body.get(token_key) if isinstance(body, dict) else None
        items = body if isinstance(body, list) else list(body.get(items_key, []))
        return Page(items=[mapper(i) for i in items],
                    next_cursor=Cursor(self.kind, "token", {"token": nxt}).encode() if nxt and not (isinstance(body, dict) and body.get("isLast")) else None)

    @property
    def site_url(self) -> str | None:
        return f"https://{self.config.site}" if self.config.site else None


def _id_of(d: dict[str, Any]) -> str:
    return str(d.get("id") or d.get("key") or "")


class McpJira(McpClient):
    """JiraIssueReader, JiraIssueSearcher, JiraProjectReader, JiraMetadataReader, JiraUserDirectory over MCP."""

    # ---- issue reader ----
    async def get_issue(self, key: str, *, fields: Sequence[str] | None = None, expand: Sequence[str] = ()) -> Issue:
        exp = list(dict.fromkeys([*expand, "names"]))
        data = await self.raw("getJiraIssue", {"issueIdOrKey": key, "fields": list(fields) if fields else None, "expand": ",".join(exp)},
                              op="jira.issue.get")
        return n.issue(data, self.site_url)

    async def list_comments(self, key: str, *, limit: int = 50, cursor: str | None = None) -> Page[Comment]:
        return await self.offset_page("listJiraIssueComments", {"issueIdOrKey": key}, "comments", n.comment, op="jira.issue.comments.list",
                                      limit=limit, cursor=cursor)

    async def list_changelog(self, key: str, *, limit: int = 100, cursor: str | None = None) -> Page[ChangelogEntry]:
        return await self.offset_page("listJiraIssueChangelogs", {"issueIdOrKey": key}, "values", n.changelog, op="jira.issue.changelog.list",
                                      limit=limit, cursor=cursor)

    async def list_worklogs(self, key: str, *, limit: int = 100, cursor: str | None = None) -> Page[Worklog]:
        return await self.offset_page("listJiraIssueWorklogs", {"issueIdOrKey": key}, "worklogs", n.worklog, op="jira.issue.worklogs.list",
                                      limit=limit, cursor=cursor)

    async def list_transitions(self, key: str) -> list[Transition]:
        data = await self.raw("listJiraIssueTransitions", {"issueIdOrKey": key}, op="jira.issue.transitions.list")
        return [n.transition(t) for t in (data if isinstance(data, list) else data.get("transitions", []))]

    async def list_attachments(self, key: str) -> list[Attachment]:
        issue = await self.get_issue(key, fields=["attachment"])
        return [n.attachment(a) for a in issue.fields.get("attachment", [])]

    async def download_attachment(self, attachment_id: str, *, dest: Path | None = None, max_bytes: int | None = None,
                                  extract_text: bool = False) -> AttachmentContent:
        data = await self.raw("downloadJiraIssueAttachment", {"attachmentId": attachment_id}, op="jira.issue.attachment.download")
        raw = _decode_file(data)
        if max_bytes is not None and len(raw) > max_bytes:
            raise error_for_status(413, f"attachment is {len(raw)} bytes, above max_bytes={max_bytes}", operation="jira.issue.attachment.download")
        mime = data.get("mimeType") if isinstance(data, dict) else None
        text = raw.decode("utf-8", "replace") if extract_text and (mime or "").startswith("text/") else None
        path = None
        if dest is not None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
            path = str(dest)
        return AttachmentContent(attachment_id=str(attachment_id), filename=data.get("filename") if isinstance(data, dict) else None, mime_type=mime,
                                 size=len(raw), data=None if dest else raw, path=path, text=text)

    async def list_links(self, key: str) -> list[IssueLink]:
        issue = await self.get_issue(key, fields=["issuelinks"])
        return [n.issue_link(x) for x in issue.fields.get("issuelinks", [])]

    async def list_remote_links(self, key: str) -> list[RemoteLink]:
        data = await self.raw("listJiraIssueRemoteIssueLinks", {"issueIdOrKey": key}, op="jira.issue.remote_links.list")
        return [n.remote_link(r) for r in (data if isinstance(data, list) else data.get("values", []))]

    async def list_watchers(self, key: str) -> list[UserRef]:
        raise unsupported("jira.issue.watchers.list")

    async def get_property(self, key: str, property_key: str) -> Any:
        data = await self.raw("getJiraEntityProperty", {"entityType": "issue", "entityIdOrKey": key, "propertyKey": property_key},
                              op="jira.issue.property.get")
        return data.get("value") if isinstance(data, dict) and "value" in data else data

    # ---- searcher ----
    async def search_issues(self, jql: str, *, fields: Sequence[str] | None = None, limit: int = 50, cursor: str | None = None,
                            reconcile_issue_ids: Sequence[int] = ()) -> Page[Issue]:
        if reconcile_issue_ids:
            raise unsupported("jira.issue.search", "reconcile_issue_ids is a REST-only consistency option")
        return await self.token_page("searchJiraIssuesUsingJql", {"jql": jql, "fields": list(fields) if fields else None}, "issues",
                                     lambda d: n.issue(d, self.site_url), op="jira.issue.search", limit=limit, cursor=cursor)

    async def count_issues(self, jql: str) -> int:
        raise unsupported("jira.issue.count")

    # ---- projects ----
    async def list_projects(self, *, query: str | None = None, limit: int = 50, cursor: str | None = None) -> Page[Project]:
        return await self.offset_page("listJiraProjects", {"query": query}, "values", n.project, op="jira.project.list", limit=limit, cursor=cursor)

    async def get_project(self, key: str) -> Project:
        page = await self.list_projects(query=key, limit=50)
        for p in page.items:
            if p.key.upper() == key.upper():
                return p
        raise NotFoundError(f"project {key} not found", hint="Check the key, or you may lack Browse permission.", operation="jira.project.get")

    async def list_components(self, project: str) -> list[Component]:
        data = await self.raw("listJiraProjectComponents", {"projectIdOrKey": project}, op="jira.project.components.list")
        return [n.component(c) for c in (data if isinstance(data, list) else data.get("values", []))]

    async def list_versions(self, project: str) -> list[Version]:
        data = await self.raw("getJiraProjectVersions", {"projectIdOrKey": project}, op="jira.project.versions.list")
        return [n.version(v) for v in (data if isinstance(data, list) else data.get("values", []))]

    # ---- metadata ----
    async def list_fields(self) -> list[Field]:
        raise unsupported("jira.meta.fields.list")

    async def list_issue_types(self, project: str | None = None) -> list[IssueType]:
        if not project:
            raise unsupported("jira.meta.issue_types.list", "the MCP tool needs a project")
        data = await self.raw("listJiraProjectIssueTypesMetadata", {"projectIdOrKey": project}, op="jira.meta.issue_types.list")
        return [n.issue_type(t) for t in (data if isinstance(data, list) else data.get("issueTypes", data.get("values", [])))]

    async def get_create_fields(self, project: str, issue_type: str) -> list[Field]:
        types = await self.list_issue_types(project)
        match = [t for t in types if issue_type in (t.id, t.name) or t.name.lower() == issue_type.lower()]
        if not match:
            raise NotFoundError(f"issue type {issue_type!r} not found in project {project}", hint="Valid types: " + ", ".join(t.name for t in types))
        data = await self.raw("getJiraIssueTypeMetaWithFields", {"projectIdOrKey": project, "issueTypeId": match[0].id},
                              op="jira.meta.create_fields")
        raw_fields = data.get("fields") or data.get("values") or []
        return [n.jfield({"id": f.get("fieldId") or f.get("key"), "name": f.get("name"), "schema": f.get("schema"), "required": f.get("required"),
                          "custom": str(f.get("fieldId", "")).startswith("customfield_")}) for f in raw_fields]

    async def list_statuses(self, project: str | None = None) -> list[Status]:
        data = await self.raw("listJiraStatuses", {"projectIdOrKey": project}, op="jira.meta.statuses.list")
        items = data if isinstance(data, list) else data.get("statuses", data.get("values", []))
        return [s for s in (n.status(x) for x in items) if s]

    async def list_priorities(self) -> list[str]:
        raise unsupported("jira.meta.priorities.list")

    async def list_link_types(self) -> list[str]:
        data = await self.raw("listJiraIssueLinkTypes", {}, op="jira.meta.link_types.list")
        return [t["name"] for t in (data if isinstance(data, list) else data.get("issueLinkTypes", []))]

    async def list_filters(self, *, query: str | None = None, limit: int = 50, cursor: str | None = None) -> Page[Filter]:
        return await self.offset_page("listJiraFilters", {"filterName": query}, "values", n.jfilter, op="jira.filter.list", limit=limit, cursor=cursor)

    async def list_dashboards(self, *, limit: int = 50, cursor: str | None = None) -> Page[Dashboard]:
        return await self.offset_page("listJiraDashboards", {}, "values", n.dashboard, op="jira.dashboard.list", limit=limit, cursor=cursor)

    async def server_info(self) -> ServerInfo:
        raise unsupported("jira.meta.server_info")

    # ---- users ----
    async def myself(self) -> UserRef:
        user = n.user(await self.raw("getJiraCurrentUser", {}, op="jira.user.myself"))
        assert user is not None
        return user

    async def get_user(self, account_id: str) -> UserRef:
        user = n.user(await self.raw("getJiraUser", {"accountId": account_id}, op="jira.user.get"))
        if user is None:
            raise NotFoundError(f"user {account_id} not found", operation="jira.user.get")
        return user

    async def search_users(self, query: str, *, limit: int = 20) -> list[UserRef]:
        data = await self.raw("lookupJiraAccountId", {"searchString": query}, op="jira.user.search")
        items = data if isinstance(data, list) else data.get("users", data.get("values", []))
        return [u for u in (n.user(d) for d in items) if u][:limit]

    async def lookup_account_id(self, query: str) -> str:
        users = await self.search_users(query, limit=10)
        exact = [u for u in users if query.lower() in ((u.email or "").lower(), (u.display_name or "").lower())]
        pool = exact or users
        if not pool:
            raise NotFoundError(f"no user matches {query!r}", hint="Try the full display name or email.", operation="jira.user.lookup_account_id")
        if len(pool) > 1:
            raise AmbiguousReferenceError(f"{len(pool)} users match {query!r}", hint="Use the email or accountId.",
                                          details={"candidates": [{"account_id": u.account_id, "name": u.display_name} for u in pool]})
        return pool[0].account_id or ""

    async def assignable_users(self, *, issue_key: str | None = None, project: str | None = None, query: str | None = None) -> list[UserRef]:
        from ...core.errors import InvalidRequestError

        if not (issue_key or project):
            raise InvalidRequestError("assignable_users needs issue_key or project", hint="Pass one of them.")
        data = await self.raw("listJiraIssueAssignableUsers", {"issueIdOrKey": issue_key, "projectKey": project, "query": query},
                              op="jira.user.assignable")
        return [u for u in (n.user(d) for d in (data if isinstance(data, list) else data.get("users", []))) if u]


def _decode_file(data: Any) -> bytes:
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, dict):
        for key in ("contentBase64", "content_base64", "base64", "data"):
            if key in data:
                return base64.b64decode(data[key])
        if "text" in data:
            return str(data["text"]).encode()
    if isinstance(data, str):
        return data.encode()
    return b""


class McpAgile(McpClient):
    async def list_boards(self, *, project: str | None = None, name: str | None = None, limit: int = 50, cursor: str | None = None) -> Page[Board]:
        return await self.offset_page("listJiraBoards", {"projectKeyOrId": project, "name": name}, "values", n.board, op="jira.agile.board.list",
                                      limit=limit, cursor=cursor)

    async def get_board(self, board_id: int) -> Board:
        raise unsupported("jira.agile.board.get")

    async def get_board_config(self, board_id: int) -> BoardConfig:
        return n.board_config(await self.raw("getJiraBoardConfig", {"boardId": board_id}, op="jira.agile.board.config"))

    async def list_board_issues(self, board_id: int, *, jql: str | None = None, limit: int = 50, cursor: str | None = None) -> Page[Issue]:
        return await self.offset_page("getJiraBoardIssueData", {"boardId": board_id, "jql": jql}, "issues", lambda d: n.issue(d, self.site_url),
                                      op="jira.agile.board.issues", limit=limit, cursor=cursor)

    async def list_sprints(self, board_id: int, *, states: Sequence[str] = ("active", "future"), limit: int = 50, cursor: str | None = None) -> Page[Sprint]:
        return await self.offset_page("listJiraBoardSprints", {"boardId": board_id, "state": ",".join(states)}, "values", n.sprint,
                                      op="jira.agile.sprint.list", limit=limit, cursor=cursor)

    async def get_sprint(self, sprint_id: int) -> Sprint:
        raise unsupported("jira.agile.sprint.get")

    async def list_sprint_issues(self, sprint_id: int, *, jql: str | None = None, limit: int = 50, cursor: str | None = None) -> Page[Issue]:
        return await self.offset_page("getJiraBoardSprintData", {"sprintId": sprint_id, "jql": jql}, "issues", lambda d: n.issue(d, self.site_url),
                                      op="jira.agile.sprint.issues", limit=limit, cursor=cursor)

    async def list_backlog(self, board_id: int, *, limit: int = 50, cursor: str | None = None) -> Page[Issue]:
        raise unsupported("jira.agile.backlog.list")

    async def list_epics(self, board_id: int, *, limit: int = 50, cursor: str | None = None) -> Page[Epic]:
        raise unsupported("jira.agile.epic.list")


class McpJsm(McpClient):
    """JSM through MCP is partial: a request is a Jira issue, so ``get_request`` works; the rest has no tool."""

    async def info(self) -> JsmInfo:
        raise unsupported("jsm.info")

    async def list_service_desks(self, *, limit: int = 50, cursor: str | None = None) -> Page[ServiceDesk]:
        raise unsupported("jsm.service_desk.list")

    async def list_request_types(self, service_desk_id: str, *, limit: int = 50, cursor: str | None = None) -> Page[RequestType]:
        raise unsupported("jsm.request_type.list")

    async def list_requests(self, *, service_desk_id: str | None = None, status: str | None = None, search: str | None = None,
                            limit: int = 50, cursor: str | None = None) -> Page[CustomerRequest]:
        raise unsupported("jsm.request.list")

    async def get_request(self, key: str) -> CustomerRequest:
        issue = n.issue(await self.raw("getJiraIssue", {"issueIdOrKey": key}, op="jsm.request.get"), self.site_url)
        return CustomerRequest(issue_id=issue.id, issue_key=issue.key, status=issue.status.name if issue.status else None, reporter=issue.reporter,
                               created=issue.created, summary=issue.summary, description=issue.description_markdown, fields=issue.fields)

    async def list_request_comments(self, key: str, *, public_only: bool = False, limit: int = 50, cursor: str | None = None) -> Page[RequestComment]:
        raise unsupported("jsm.request.comments")

    async def get_request_sla(self, key: str) -> list[SlaInfo]:
        raise unsupported("jsm.request.sla")

    async def search_knowledge_base(self, query: str, *, service_desk_id: str | None = None, limit: int = 20) -> list[KbArticle]:
        raise unsupported("jsm.kb.search")

    async def list_organizations(self, *, limit: int = 50, cursor: str | None = None) -> Page[Organization]:
        raise unsupported("jsm.organization.list")

    async def list_customers(self, service_desk_id: str, *, limit: int = 50, cursor: str | None = None) -> Page[UserRef]:
        raise unsupported("jsm.customer.list")


class McpConfluence(McpClient):
    """ConfluenceContentReader, ConfluenceSearcher, ConfluenceSpaceReader, ConfluenceTemplateReader over MCP."""

    def _content(self, d: dict[str, Any], type_: ContentType = ContentType.PAGE) -> Content:
        return n.content(d, type_=type_.value, base=f"{self.site_url}/wiki" if self.site_url else None)

    async def get_content(self, content_id: str, *, type: ContentType = ContentType.PAGE, body_format: BodyFormat = "markdown",
                          version: int | None = None) -> Content:
        wire = {"markdown": "storage", "storage": "storage", "atlas_doc_format": "atlas_doc_format", "view": "view", "none": None}[body_format]
        data = await self.raw("getConfluenceContent", {"contentId": content_id, "contentType": type.value, "bodyFormat": wire, "version": version},
                              op="confluence.content.get")
        return self._content(data, type)

    async def list_content(self, *, space_id: str | None = None, type: ContentType = ContentType.PAGE, status: str = "current",
                           title: str | None = None, limit: int = 25, cursor: str | None = None) -> Page[Content]:
        return await self.token_page("listConfluenceContent", {"spaceId": space_id, "contentType": type.value, "status": status, "title": title},
                                     "results", lambda d: self._content(d, type), op="confluence.content.list", limit=limit, cursor=cursor,
                                     token_arg="cursor", token_key="nextCursor")

    async def list_children(self, content_id: str, *, type: ContentType = ContentType.PAGE, limit: int = 25, cursor: str | None = None) -> Page[Content]:
        return await self.token_page("listConfluenceContent", {"parentId": content_id, "contentType": type.value}, "results",
                                     lambda d: self._content(d, type), op="confluence.content.children", limit=limit, cursor=cursor,
                                     token_arg="cursor", token_key="nextCursor")

    async def list_ancestors(self, content_id: str, *, type: ContentType = ContentType.PAGE) -> list[Content]:
        raise unsupported("confluence.content.ancestors")

    async def list_descendants(self, content_id: str, *, depth: int | None = None, limit: int = 50, cursor: str | None = None) -> Page[Content]:
        raise unsupported("confluence.content.descendants")

    async def list_versions(self, content_id: str, *, limit: int = 25, cursor: str | None = None) -> Page[ContentVersion]:
        return await self.token_page("listConfluenceContentVersions", {"contentId": content_id}, "results", n.content_version,
                                     op="confluence.content.versions", limit=limit, cursor=cursor, token_arg="cursor", token_key="nextCursor")

    async def diff_versions(self, content_id: str, from_version: int, to_version: int) -> ContentDiff:
        data = await self.raw("diffConfluenceContentVersions", {"contentId": content_id, "fromVersion": from_version, "toVersion": to_version},
                              op="confluence.content.diff")
        unified = data.get("diff") or data.get("unified") or "" if isinstance(data, dict) else str(data)
        return ContentDiff(content_id=content_id, from_version=from_version, to_version=to_version, unified=unified)

    async def list_labels(self, content_id: str) -> list[Label]:
        data = await self.raw("getConfluenceContent", {"contentId": content_id, "bodyFormat": None}, op="confluence.label.list")
        raw_labels: Any = data.get("labels") if isinstance(data, dict) else None
        labels: list[Any] = list(raw_labels.get("results", [])) if isinstance(raw_labels, dict) else list(raw_labels or [])
        return [n.label(x) if isinstance(x, dict) else Label(name=str(x)) for x in labels]

    async def get_properties(self, content_id: str) -> dict[str, Any]:
        raise unsupported("confluence.property.get")

    async def list_comments(self, content_id: str, *, kind: Literal["footer", "inline", "all"] = "all", limit: int = 25,
                            cursor: str | None = None) -> Page[ConfluenceComment]:
        data = await self.raw("listConfluenceComments", {"contentId": content_id, "kind": None if kind == "all" else kind, "limit": limit},
                              op="confluence.comment.list")
        items = data if isinstance(data, list) else data.get("results", [])
        return Page(items=[n.conf_comment(c, c.get("kind", "footer")) for c in items])

    async def list_attachments(self, content_id: str, *, limit: int = 25, cursor: str | None = None) -> Page[CAttachment]:
        return await self.token_page("listConfluenceAttachments", {"contentId": content_id}, "results", n.conf_attachment,
                                     op="confluence.attachment.list", limit=limit, cursor=cursor, token_arg="cursor", token_key="nextCursor")

    async def download_attachment(self, attachment_id: str, *, dest: Path | None = None, max_bytes: int | None = None,
                                  extract_text: bool = False) -> CAttachmentContent:
        data = await self.raw("downloadConfluenceAttachment", {"attachmentId": attachment_id}, op="confluence.attachment.download")
        raw = _decode_file(data)
        if max_bytes is not None and len(raw) > max_bytes:
            raise error_for_status(413, f"attachment is {len(raw)} bytes, above max_bytes={max_bytes}", operation="confluence.attachment.download")
        mime = data.get("mediaType") or data.get("mimeType") if isinstance(data, dict) else None
        path = None
        if dest is not None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
            path = str(dest)
        text = raw.decode("utf-8", "replace") if extract_text and (mime or "").startswith("text/") else None
        return CAttachmentContent(attachment_id=attachment_id, filename=data.get("title") or data.get("filename") if isinstance(data, dict) else None,
                                  mime_type=mime, size=len(raw), data=None if dest else raw, path=path, text=text)

    async def get_permissions(self, content_id: str) -> Permissions:
        data = await self.raw("getConfluenceContentPermissions", {"contentId": content_id}, op="confluence.permission.get")
        return Permissions(operations=list(data.get("operations", data) if isinstance(data, dict) else data))

    async def get_restrictions(self, content_id: str) -> Restrictions:
        return n.restrictions(await self.raw("getConfluenceContentRestrictionState", {"contentId": content_id}, op="confluence.permission.restrictions.get"))

    async def list_tasks(self, *, space_id: str | None = None, content_id: str | None = None,
                         status: Literal["complete", "incomplete"] | None = None, limit: int = 25, cursor: str | None = None) -> Page[Task]:
        return await self.token_page("listConfluenceTasks", {"spaceId": space_id, "contentId": content_id, "status": status}, "results", n.conf_task,
                                     op="confluence.task.list", limit=limit, cursor=cursor, token_arg="cursor", token_key="nextCursor")

    async def resolve_macros(self, content_id: str) -> Content:
        data = await self.raw("resolveConfluenceContentMacros", {"contentId": content_id}, op="confluence.content.macros.resolve")
        c = self._content(data if isinstance(data, dict) and "id" in data else {"id": content_id, "body": {"view": {"value": str(data)}}})
        if isinstance(data, dict) and "html" in data:
            c.body_markdown = storage_to_markdown(data["html"])
        return c

    async def search_cql(self, cql: str, *, limit: int = 25, cursor: str | None = None, excerpt: bool = True) -> Page[SearchHit]:
        return await self.token_page("searchConfluence", {"cql": cql}, "results", n.search_hit, op="confluence.search.cql", limit=limit,
                                     cursor=cursor, token_arg="cursor", token_key="nextCursor")

    async def list_spaces(self, *, keys: Sequence[str] = (), type: str | None = None, limit: int = 25, cursor: str | None = None) -> Page[Space]:
        return await self.token_page("listConfluenceSpaces", {"keys": ",".join(keys) or None, "type": type}, "results", n.space,
                                     op="confluence.space.list", limit=limit, cursor=cursor, token_arg="cursor", token_key="nextCursor")

    async def get_space(self, key_or_id: str) -> Space:
        return n.space(await self.raw("getConfluenceSpace", {"spaceIdOrKey": key_or_id}, op="confluence.space.get"))

    async def get_personal_space(self, account_id: str | None = None) -> Space:
        return n.space(await self.raw("getConfluencePersonalSpace", {"accountId": account_id}, op="confluence.space.personal"))

    async def get_space_instructions(self, key_or_id: str) -> str | None:
        data = await self.raw("getConfluenceSpaceInstructions", {"spaceIdOrKey": key_or_id}, op="confluence.space.instructions.get")
        if isinstance(data, dict):
            return data.get("instructions")
        return str(data) if data else None

    async def list_space_permissions(self, key_or_id: str, *, limit: int = 50, cursor: str | None = None) -> Page[dict[str, Any]]:
        raise unsupported("confluence.space.permissions.list")

    async def list_templates(self, *, space_key: str | None = None) -> list[Template]:
        data = await self.raw("listConfluenceTemplates", {"spaceKey": space_key}, op="confluence.template.list")
        return [n.template(t) for t in (data if isinstance(data, list) else data.get("results", []))]

    async def get_template(self, template_id: str) -> Template:
        return n.template(await self.raw("getConfluenceTemplate", {"templateId": template_id}, op="confluence.template.get"))


class McpGraph(McpClient):
    """Teamwork Graph and Rovo semantic search. Both consume Rovo credits (the policy gate is in PolicyGuard)."""

    async def get_context(self, ari_or_url: str, *, max_items: int = 50) -> GraphContext:
        data = await self.raw("getTeamworkGraphContext", {"ariOrUrl": ari_or_url, "maxItems": max_items}, op="graph.context")
        related = [GraphObject(ari=str(o.get("ari", "")), type=o.get("type"), title=o.get("title"), url=o.get("url"),
                               properties=o.get("properties", {})) for o in data.get("related", [])]
        return GraphContext(root=ari_or_url, related=related, relationships=list(data.get("relationships", [])))

    async def get_objects(self, aris_or_urls: Sequence[str]) -> list[GraphObject]:
        data = await self.raw("getTeamworkGraphObject", {"ariOrUrls": list(aris_or_urls)}, op="graph.object.get")
        objects: list[Any] = data if isinstance(data, list) else list(data.get("objects", []))
        return [GraphObject(ari=str(o.get("ari", "")), type=o.get("type"), title=o.get("title"), url=o.get("url"), properties=o.get("properties", {}))
                for o in objects]

    async def semantic_search(self, query: str, *, products: Sequence[str] = (), limit: int = 20) -> list[SemanticHit]:
        data = await self.raw("search", {"query": query, "products": list(products) or None, "limit": limit}, op="graph.search.semantic")
        return [SemanticHit(title=h.get("title", ""), url=h.get("url"), product=h.get("product"), snippet=h.get("snippet"), score=h.get("score"))
                for h in (data if isinstance(data, list) else data.get("results", []))][:limit]

    async def add_context(self, ari_or_url: str, context: Any) -> None:
        await self.raw("addTeamworkGraphContext", {"ariOrUrl": ari_or_url, "context": dict(context)}, op="graph.context.add")


class McpIdentity(McpClient):
    async def whoami(self) -> Identity:
        data = await self.raw("atlassianUserInfo", {}, op="identity.whoami", needs_cloud=False)
        return Identity(account_id=data.get("account_id") or data.get("accountId"), display_name=data.get("name") or data.get("displayName"),
                        email=data.get("email"), site=self.config.site, cloud_id=self._cloud_id, auth_scheme="mcp", backend="mcp")

    async def accessible_resources(self) -> list[AccessibleResource]:
        data = await self.raw("getAccessibleAtlassianResources", {}, op="identity.accessible_resources", needs_cloud=False)
        sites = data if isinstance(data, list) else data.get("resources", data.get("sites", []))
        return [AccessibleResource(id=str(s.get("id") or s.get("cloudId")), name=s.get("name"), url=s.get("url"), scopes=list(s.get("scopes", [])))
                for s in sites]


class McpBackend:
    """Atlassian Remote MCP v2: read/search for Jira, Confluence and the Teamwork Graph."""

    kind: ClassVar[BackendKind] = BackendKind.MCP
    deployments: ClassVar[frozenset[Deployment]] = frozenset({Deployment.CLOUD})

    def __init__(self, transport: McpTransport, config: AtlassianConfig) -> None:
        self.transport, self.config = transport, config
        self.jira, self.agile, self.jsm = McpJira(transport, config), McpAgile(transport, config), McpJsm(transport, config)
        self.confluence, self.graph, self.identity = McpConfluence(transport, config), McpGraph(transport, config), McpIdentity(transport, config)
        from .write import McpAgileWrite, McpConfluenceWrite, McpJiraWrite, McpJsmWrite

        self.jira_write, self.agile_write, self.jsm_write = McpJiraWrite(transport, config), McpAgileWrite(transport, config), McpJsmWrite(transport, config)
        self.confluence_write = McpConfluenceWrite(transport, config)
        self._parts: tuple[McpClient, ...] = (self.jira, self.agile, self.jsm, self.confluence, self.graph, self.identity, self.jira_write,
                                              self.agile_write, self.jsm_write, self.confluence_write)
        self._live: set[str] | None = None

    def implementation_for(self, protocol: type) -> object | None:
        return next((p for p in self._parts if isinstance(p, protocol)), None)

    def supported_operations(self) -> frozenset[str]:
        """Operations served by MCP tools. Narrowed to the live ``tools/list`` once a session has been opened."""
        live = self.jira._tools
        return frozenset(op for op, tool in ALL_OP_TOOLS.items() if live is None or tool in live)

    async def connect(self) -> frozenset[str]:
        """Open the session, learn which tools the admin enabled, and return the operations actually available."""
        tools = await self.jira.tools()
        for part in self._parts:
            part._tools = tools
        return self.supported_operations()

    async def health(self) -> BackendHealth:
        try:
            ops = await self.connect()
        except Exception as exc:
            return BackendHealth("unavailable", f"{type(exc).__name__}: {exc}")
        return BackendHealth("ready", f"{len(ops)} operations available from the live tool list")

    async def aclose(self) -> None:
        await self.transport.aclose()


class McpBackendFactory:
    kind: ClassVar[BackendKind] = BackendKind.MCP
    deployments: ClassVar[frozenset[Deployment]] = frozenset({Deployment.CLOUD})
    extra: ClassVar[str | None] = "mcp"

    def __init__(self, transport_factory: Callable[[AtlassianConfig], McpTransport] | None = None) -> None:
        self._transport_factory = transport_factory

    def availability(self, config: AtlassianConfig) -> Availability:
        if self._transport_factory is None:
            try:
                import mcp  # noqa: F401
            except ImportError:
                return Availability(False, "pip install 'atlassian-client[mcp]'")
        if not (config.email and config.api_token) and not config.service_account_key:
            return Availability(False, "MCP needs ATLASSIAN_EMAIL + ATLASSIAN_API_TOKEN or ATLASSIAN_SERVICE_ACCOUNT_KEY "
                                       "(API-token auth must be enabled by the org admin); OAuth 2.1 is not wired yet")
        return Availability(True)

    def create(self, config: AtlassianConfig, core: CoreServices) -> McpBackend:
        if self._transport_factory is not None:
            return McpBackend(self._transport_factory(config), config)
        import base64 as b64

        if config.service_account_key:
            headers = {"Authorization": f"Bearer {config.service_account_key.get_secret_value()}"}
        else:
            assert config.email and config.api_token
            headers = {"Authorization": "Basic " + b64.b64encode(f"{config.email}:{config.api_token.get_secret_value()}".encode()).decode()}
        url = str(config.mcp_url) + ("?tools=all" if config.mcp_flat_tools else "")
        return McpBackend(SdkMcpTransport(url, headers), config)


ALL_OP_TOOLS.update(_all_op_tools())
__all__ = ["ALL_OP_TOOLS", "OP_TOOLS", "McpBackend", "McpBackendFactory", "AtlassianError", "BackendUnavailableError", "ProjectRef"]
