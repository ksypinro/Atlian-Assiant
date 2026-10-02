"""Generates atlassian_client/facade/_generated_*.py from the method specs below (design section 13).

Every generated method has the standard agent-facing docstring (summary, When to use, Args, Returns, Raises, Access,
Example) and routes through ``Namespace._call``. Edit the SPECS here, then run ``python -m tools.gen_facade``.
"""
from __future__ import annotations

import ast
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from atlassian_client import operations as ops  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "atlassian_client" / "facade"

COMMON = {
    "key": 'Issue key such as "PROJ-123" (case-insensitive) or the numeric issue id.',
    "fields": "Fields to return. Human names (\"Story Points\") and ids (\"customfield_10016\") are both accepted. "
              "None returns the navigable defaults. Request only what you need in bulk jobs.",
    "expand": 'Optional expansions, e.g. ("renderedFields", "changelog").',
    "limit": "Maximum items in this page.",
    "cursor": "`next_cursor` from a previous page. Pass it back unchanged; never edit it.",
    "jql": "JQL query. Validate unfamiliar JQL with `jira.jql.validate` first.",
    "cql": 'CQL query, e.g. \'space = "ENG" AND type = page AND text ~ "runbook"\'.',
    "project": 'Project key such as "PROJ".',
    "content_id": "Confluence content id (digits).",
    "space_key": "Confluence space key.",
    "key_or_id": "Space key (\"ENG\") or numeric space id.",
    "query": "Free-text query.",
    "board_id": "Numeric board id (from `jira.agile.boards`).",
    "sprint_id": "Numeric sprint id.",
    "dest": "Optional path to save the file to. When omitted the bytes are returned in memory.",
    "max_bytes": "Refuse to download more than this many bytes.",
    "extract_text": "Also return decoded text for text/* files (for agents).",
    "account_id": "Atlassian accountId.",
    "service_desk_id": "Numeric service desk id (from `jsm.service_desks`).",
    "type": 'Content type: page, blogpost, whiteboard, database, folder, embed or custom_content.',
    "body_format": '"markdown" (default), "storage", "atlas_doc_format", "view" or "none".',
    "space_id": "Numeric space id.",
    "ari_or_url": "Atlassian Resource Identifier or the entity URL.",
    "comment": "Optional Markdown comment.",
    "enabled": "True to enable, False to disable.",
    "prompt": "What it should show or do.",
    "spec": "Description of the app to build.",
    "dry_run": "True returns the PlannedAction (what WOULD happen) without changing anything. Use it first, show the user, then call again.",
    "draft": "The new item's fields.",
    "update": "The changes to apply.",
    "body": "Text as Markdown (default), or a BodyInput for ADF / storage / plain.",
    "comment_id": "Comment id.",
    "notify_users": "Send watcher notifications.",
    "assignee": "accountId, email or display name (resolved), or None to unassign.",
    "transition": "Transition name or id (see `jira.issues.transitions`).",
    "link_type": "Link type name (see `jira.metadata.link_types`).",
    "title": "Title.",
    "url": "URL.",
    "property_key": "Property key.",
    "value": "JSON-serializable value.",
    "attachment_id": "Attachment id.",
    "file": "File path or raw bytes.",
    "filename": "File name to store (defaults to the path's name).",
    "content_ids": "Confluence content ids.",
    "purge": "Permanently delete instead of moving to trash.",
    "labels": "Label names.",
    "label": "Label name.",
    "emoji": "Emoji name.",
    "grants": "Grants like {\"operation\": \"read\", \"group\": \"eng\"} or {\"operation\": \"update\", \"user\": accountId}.",
    "change": "The change to apply.",
    "issue_keys": "Issue keys.",
    "name": "Name.",
    "key": 'Key (e.g. "PROJ-123" for issues, "PROJ" for projects).',
    "kind": "Which kind.",
    "status": "Status filter.",
    "depth": "Maximum depth.",
    "backend": 'Force one backend ("rest", "mcp", "acli", ...). Default: the preset order. A cursor pins its backend.',
}


@dataclass
class M:
    name: str
    op: str
    proto: str
    method: str
    sig: str
    ret: str
    summary: str
    when: str
    example: str
    docs: dict[str, str] = field(default_factory=dict)
    expand: str | None = None          # name of a dict parameter passed to the protocol as **kwargs
    raises: str = "NoBackendAvailableError: No configured backend can serve this operation."


SPECS: list[tuple[str, str, str, list[M]]] = []   # (class, doc, protocols import, methods)


def cls(name: str, doc: str, protos: str, methods: list[M]) -> None:
    SPECS.append((name, doc, protos, methods))


cls("JiraIssuesClient", "Jira issues (work items): read, search and inspect.", "jira", [
    M("get", "jira.issue.get", "JiraIssueReader", "get_issue", "key: str, *, fields: Sequence[str] | None = None, expand: Sequence[str] = ()",
      "Issue", "Fetch one Jira issue by key or numeric id.",
      'You already know the issue key ("PROJ-123"). To find issues by criteria use `search` with JQL.',
      'issue = await client.jira.issues.get("PROJ-123", fields=["summary", "status"])',
      raises="NotFoundError: The issue does not exist OR you lack Browse permission (Atlassian returns 404 for both)."),
    M("search", "jira.issue.search", "JiraIssueSearcher", "search_issues",
      'jql: str, *, fields: Sequence[str] | None = ("summary", "status", "assignee", "updated"), limit: int = 50, cursor: str | None = None, reconcile_issue_ids: Sequence[int] = ()',
      "Page[Issue]", "Search issues with JQL and return ONE page of results.",
      "Precise filtering. Prefer exact JQL when the user states exact filters, e.g. 'project = MOBILE AND status != Done ORDER BY updated DESC'.",
      'page = await client.jira.issues.search("assignee = currentUser() AND resolution = EMPTY")',
      docs={"reconcile_issue_ids": "Issue ids recently written by you; Jira then guarantees read-after-write consistency for them."},
      raises="InvalidRequestError: JQL is invalid; `details` lists Atlassian's messages."),
    M("count", "jira.issue.count", "JiraIssueSearcher", "count_issues", "jql: str", "int",
      "Approximate number of issues matching a JQL query.", "You need a total without fetching issues.",
      'n = await client.jira.issues.count("project = PROJ AND resolution = EMPTY")'),
    M("comments", "jira.issue.comments.list", "JiraIssueReader", "list_comments", "key: str, *, limit: int = 50, cursor: str | None = None",
      "Page[Comment]", "List the comments of an issue.", "You need the discussion on one issue.",
      'page = await client.jira.issues.comments("PROJ-123")'),
    M("changelog", "jira.issue.changelog.list", "JiraIssueReader", "list_changelog", "key: str, *, limit: int = 100, cursor: str | None = None",
      "Page[ChangelogEntry]", "List the change history of an issue.", "You need to know who changed what and when.",
      'page = await client.jira.issues.changelog("PROJ-123")'),
    M("worklogs", "jira.issue.worklogs.list", "JiraIssueReader", "list_worklogs", "key: str, *, limit: int = 100, cursor: str | None = None",
      "Page[Worklog]", "List the time logged on an issue.", "You need time-tracking details.",
      'page = await client.jira.issues.worklogs("PROJ-123")'),
    M("transitions", "jira.issue.transitions.list", "JiraIssueReader", "list_transitions", "key: str", "list[Transition]",
      "List the workflow transitions currently available for an issue.", "Before transitioning an issue, to learn valid transition names.",
      'ts = await client.jira.issues.transitions("PROJ-123")'),
    M("links", "jira.issue.links.list", "JiraIssueReader", "list_links", "key: str", "list[IssueLink]",
      "List issue links (blocks, relates to, ...).", "You need related issues.", 'links = await client.jira.issues.links("PROJ-123")'),
    M("remote_links", "jira.issue.remote_links.list", "JiraIssueReader", "list_remote_links", "key: str", "list[RemoteLink]",
      "List remote links (web pages, Confluence pages, ...).", "You need external references of an issue.",
      'links = await client.jira.issues.remote_links("PROJ-123")'),
    M("attachments", "jira.issue.attachments.list", "JiraIssueReader", "list_attachments", "key: str", "list[JiraAttachment]",
      "List attachment metadata of an issue (no content is downloaded).", "Before downloading, to pick the attachment you need.",
      'files = await client.jira.issues.attachments("PROJ-123")'),
    M("download_attachment", "jira.issue.attachment.download", "JiraIssueReader", "download_attachment",
      "attachment_id: str, *, dest: Path | None = None, max_bytes: int | None = None, extract_text: bool = False", "JiraAttachmentContent",
      "Download one issue attachment.", "You need the content of a specific attachment.",
      'content = await client.jira.issues.download_attachment("10042", max_bytes=1_000_000, extract_text=True)',
      docs={"attachment_id": "Attachment id from `attachments`."}),
    M("watchers", "jira.issue.watchers.list", "JiraIssueReader", "list_watchers", "key: str", "list[UserRef]",
      "List the users watching an issue.", "You need to know who follows an issue.", 'who = await client.jira.issues.watchers("PROJ-123")'),
    M("get_property", "jira.issue.property.get", "JiraIssueReader", "get_property", "key: str, property_key: str", "Any",
      "Read an issue property (app-defined JSON value).", "An app stored metadata on the issue.",
      'value = await client.jira.issues.get_property("PROJ-123", "my.app.state")', docs={"property_key": "The property key."}),
])
cls("JiraProjectsClient", "Jira projects, components and versions.", "jira", [
    M("list", "jira.project.list", "JiraProjectReader", "list_projects", "*, query: str | None = None, limit: int = 50, cursor: str | None = None",
      "Page[Project]", "List or search projects you can see.", "You need project keys.", "page = await client.jira.projects.list(query=\"mobile\")"),
    M("get", "jira.project.get", "JiraProjectReader", "get_project", "key: str", "Project", "Fetch one project.", "You know the project key.",
      'p = await client.jira.projects.get("PROJ")', docs={"key": "Project key."}),
    M("components", "jira.project.components.list", "JiraProjectReader", "list_components", "project: str", "list[Component]",
      "List the components of a project.", "You need valid component names.", 'cs = await client.jira.projects.components("PROJ")'),
    M("versions", "jira.project.versions.list", "JiraProjectReader", "list_versions", "project: str", "list[Version]",
      "List the versions (releases) of a project.", "You need release information.", 'vs = await client.jira.projects.versions("PROJ")'),
])
cls("JiraMetadataClient", "Jira fields, issue types, statuses and server information.", "jira", [
    M("fields", "jira.meta.fields.list", "JiraMetadataReader", "list_fields", "", "list[Field]", "List all fields (system and custom).",
      "You need a field id or want to know which custom fields exist.", "fields = await client.jira.metadata.fields()"),
    M("issue_types", "jira.meta.issue_types.list", "JiraMetadataReader", "list_issue_types", "project: str | None = None", "list[IssueType]",
      "List issue types, optionally for one project.", "You need valid issue type names.", 'ts = await client.jira.metadata.issue_types("PROJ")'),
    M("create_fields", "jira.meta.create_fields", "JiraMetadataReader", "get_create_fields", "project: str, issue_type: str", "list[Field]",
      "List the fields available when creating an issue of a type in a project.", "Before creating an issue.",
      'fs = await client.jira.metadata.create_fields("PROJ", "Bug")', docs={"issue_type": "Issue type name or id."}),
    M("statuses", "jira.meta.statuses.list", "JiraMetadataReader", "list_statuses", "project: str | None = None", "list[Status]",
      "List statuses, optionally for one project.", "You need status names for JQL.", 'ss = await client.jira.metadata.statuses("PROJ")'),
    M("priorities", "jira.meta.priorities.list", "JiraMetadataReader", "list_priorities", "", "list[str]", "List priority names.",
      "You need valid priorities.", "ps = await client.jira.metadata.priorities()"),
    M("link_types", "jira.meta.link_types.list", "JiraMetadataReader", "list_link_types", "", "list[str]", "List issue link type names.",
      "You need valid link types.", "ls = await client.jira.metadata.link_types()"),
    M("server_info", "jira.meta.server_info", "JiraMetadataReader", "server_info", "", "ServerInfo", "Jira version and deployment information.",
      "You need to know the Jira version or time zone.", "info = await client.jira.metadata.server_info()"),
])
cls("JiraUsersClient", "Jira users and account lookup.", "jira", [
    M("myself", "jira.user.myself", "JiraUserDirectory", "myself", "", "UserRef", "The user the credentials belong to.", "You need your own accountId.",
      "me = await client.jira.users.myself()"),
    M("get", "jira.user.get", "JiraUserDirectory", "get_user", "account_id: str", "UserRef", "Fetch one user by accountId.", "You have an accountId.",
      'u = await client.jira.users.get("5b10a2844c20165700ede21g")'),
    M("search", "jira.user.search", "JiraUserDirectory", "search_users", "query: str, *, limit: int = 20", "list[UserRef]",
      "Search users by name or email.", "You need to find a person.", 'us = await client.jira.users.search("ann")'),
    M("lookup_account_id", "jira.user.lookup_account_id", "JiraUserDirectory", "lookup_account_id", "query: str", "str",
      "Resolve a name or email to exactly one accountId.", "You need an accountId for assigning or mentioning.",
      'aid = await client.jira.users.lookup_account_id("ann@example.com")',
      raises="AmbiguousReferenceError: More than one user matches; candidates are in `details`."),
    M("assignable", "jira.user.assignable", "JiraUserDirectory", "assignable_users",
      "*, issue_key: str | None = None, project: str | None = None, query: str | None = None", "list[UserRef]",
      "List users who can be assigned an issue or issues in a project.", "Before assigning.",
      'us = await client.jira.users.assignable(project="PROJ")', docs={"issue_key": "Issue key.", "project": "Project key."}),
])
cls("JiraFiltersClient", "Saved Jira filters.", "jira", [
    M("list", "jira.filter.list", "JiraMetadataReader", "list_filters", "*, query: str | None = None, limit: int = 50, cursor: str | None = None",
      "Page[Filter]", "List or search saved filters.", "You need a saved JQL.", 'page = await client.jira.filters.list(query="sprint")'),
])
cls("JiraDashboardsClient", "Jira dashboards.", "jira", [
    M("list", "jira.dashboard.list", "JiraMetadataReader", "list_dashboards", "*, limit: int = 50, cursor: str | None = None", "Page[Dashboard]",
      "List dashboards.", "You need a dashboard.", "page = await client.jira.dashboards.list()"),
])
cls("JiraAgileClient", "Jira Software boards, sprints, backlog and epics.", "agile", [
    M("boards", "jira.agile.board.list", "JiraAgileReader", "list_boards",
      "*, project: str | None = None, name: str | None = None, limit: int = 50, cursor: str | None = None", "Page[Board]",
      "List boards.", "You need a board id.", 'page = await client.jira.agile.boards(project="MOBILE")', docs={"name": "Board name filter."}),
    M("board", "jira.agile.board.get", "JiraAgileReader", "get_board", "board_id: int", "Board", "Fetch one board.", "You have a board id.",
      "b = await client.jira.agile.board(42)"),
    M("board_config", "jira.agile.board.config", "JiraAgileReader", "get_board_config", "board_id: int", "BoardConfig",
      "Board configuration (columns, filter, estimation).", "You need to understand a board.", "cfg = await client.jira.agile.board_config(42)"),
    M("board_issues", "jira.agile.board.issues", "JiraAgileReader", "list_board_issues",
      "board_id: int, *, jql: str | None = None, limit: int = 50, cursor: str | None = None", "Page[Issue]",
      "Issues on a board.", "You need what is on a board.", "page = await client.jira.agile.board_issues(42)"),
    M("sprints", "jira.agile.sprint.list", "JiraAgileReader", "list_sprints",
      'board_id: int, *, states: Sequence[str] = ("active", "future"), limit: int = 50, cursor: str | None = None', "Page[Sprint]",
      "List sprints of a board.", "You need the active sprint.", "page = await client.jira.agile.sprints(42)",
      docs={"states": "Sprint states: active, future, closed."}),
    M("sprint", "jira.agile.sprint.get", "JiraAgileReader", "get_sprint", "sprint_id: int", "Sprint", "Fetch one sprint.", "You have a sprint id.",
      "s = await client.jira.agile.sprint(7)"),
    M("sprint_issues", "jira.agile.sprint.issues", "JiraAgileReader", "list_sprint_issues",
      "sprint_id: int, *, jql: str | None = None, limit: int = 50, cursor: str | None = None", "Page[Issue]",
      "Issues in a sprint.", "Summarize or inspect a sprint.", "page = await client.jira.agile.sprint_issues(7)"),
    M("backlog", "jira.agile.backlog.list", "JiraAgileReader", "list_backlog", "board_id: int, *, limit: int = 50, cursor: str | None = None",
      "Page[Issue]", "Issues in the backlog of a board.", "Planning.", "page = await client.jira.agile.backlog(42)"),
    M("epics", "jira.agile.epic.list", "JiraAgileReader", "list_epics", "board_id: int, *, limit: int = 50, cursor: str | None = None",
      "Page[Epic]", "Epics of a board.", "Roadmap questions.", "page = await client.jira.agile.epics(42)"),
])
cls("JiraJqlClient", "JQL helper tools: parse, sanitize, autocomplete, match.", "jira", [
    M("parse", "jira.jql.parse", "JiraJqlTools", "parse_jql", 'queries: Sequence[str], *, validation: str = "strict"', "list[JqlParseResult]",
      "Parse and validate JQL queries.", "Before running unfamiliar JQL.", 'r = await client.jira.jql.parse(["project = PROJ"])',
      docs={"queries": "JQL queries to parse.", "validation": "strict, warn or none."}),
    M("sanitize", "jira.jql.sanitize", "JiraJqlTools", "sanitize_jql", "queries: Sequence[str]", "list[str]",
      "Replace user-specific data (names) in JQL with account ids.", "JQL came from a human or an LLM.",
      'q = await client.jira.jql.sanitize(["assignee = ann"])', docs={"queries": "JQL queries."}),
    M("autocomplete", "jira.jql.autocomplete", "JiraJqlTools", "autocomplete", "*, field_name: str | None = None, field_value: str | None = None",
      "dict[str, Any]", "JQL field and value suggestions.", "Building a query interactively.", 'r = await client.jira.jql.autocomplete(field_name="status")',
      docs={"field_name": "Field to suggest values for.", "field_value": "Partial value typed so far."}),
    M("match", "jira.jql.match", "JiraJqlTools", "match_jql", "issue_ids: Sequence[int], jqls: Sequence[str]", "dict[str, list[int]]",
      "Which of the given issues match each JQL query.", "Checking issues against several queries at once.",
      'r = await client.jira.jql.match([10001], ["project = PROJ"])', docs={"issue_ids": "Numeric issue ids.", "jqls": "JQL queries."}),
])
cls("JiraExpressionsClient", "Jira expressions.", "jira", [
    M("evaluate", "jira.expression.evaluate", "JiraJqlTools", "evaluate_expression", "expression: str, context: dict[str, Any]", "Any",
      "Evaluate a Jira expression.", "Computing derived values server-side.", 'v = await client.jira.expressions.evaluate("1 + 1", {})',
      docs={"expression": "Jira expression.", "context": "Evaluation context."}),
])
cls("JsmClient", "Jira Service Management: requests, SLAs, knowledge base, organizations.", "jsm", [
    M("info", "jsm.info", "JsmReader", "info", "", "JsmInfo", "JSM version information.", "Checking JSM availability.", "i = await client.jsm.info()"),
    M("service_desks", "jsm.service_desk.list", "JsmReader", "list_service_desks", "*, limit: int = 50, cursor: str | None = None", "Page[ServiceDesk]",
      "List service desks.", "You need a service desk id.", "page = await client.jsm.service_desks()"),
    M("request_types", "jsm.request_type.list", "JsmReader", "list_request_types", "service_desk_id: str, *, limit: int = 50, cursor: str | None = None",
      "Page[RequestType]", "List request types of a service desk.", "Before creating a request.", 'page = await client.jsm.request_types("4")'),
    M("requests", "jsm.request.list", "JsmReader", "list_requests",
      "*, service_desk_id: str | None = None, status: str | None = None, search: str | None = None, limit: int = 50, cursor: str | None = None",
      "Page[CustomerRequest]", "List customer requests.", "Triage or search requests.", 'page = await client.jsm.requests(status="OPEN_REQUESTS")',
      docs={"status": "ALL_REQUESTS, OPEN_REQUESTS or CLOSED_REQUESTS.", "search": "Text to search for."}),
    M("request", "jsm.request.get", "JsmReader", "get_request", "key: str", "CustomerRequest", "Fetch one customer request.", "You know the request key.",
      'r = await client.jsm.request("SD-12")'),
    M("comments", "jsm.request.comments", "JsmReader", "list_request_comments",
      "key: str, *, public_only: bool = False, limit: int = 50, cursor: str | None = None", "Page[RequestComment]",
      "Comments on a request (public and internal).", "Reading the conversation.", 'page = await client.jsm.comments("SD-12")',
      docs={"public_only": "Only customer-visible comments."}),
    M("sla", "jsm.request.sla", "JsmReader", "get_request_sla", "key: str", "list[SlaInfo]", "SLA status of a request.", "Checking breaches.",
      'sla = await client.jsm.sla("SD-12")'),
    M("kb_search", "jsm.kb.search", "JsmReader", "search_knowledge_base", "query: str, *, service_desk_id: str | None = None, limit: int = 20",
      "list[KbArticle]", "Search knowledge-base articles.", "Answering a support question.", 'a = await client.jsm.kb_search("vpn")'),
    M("organizations", "jsm.organization.list", "JsmReader", "list_organizations", "*, limit: int = 50, cursor: str | None = None",
      "Page[Organization]", "List customer organizations.", "Customer management.", "page = await client.jsm.organizations()"),
    M("customers", "jsm.customer.list", "JsmReader", "list_customers", "service_desk_id: str, *, limit: int = 50, cursor: str | None = None",
      "Page[UserRef]", "List customers of a service desk.", "Customer management.", 'page = await client.jsm.customers("4")'),
])
cls("ConfluenceContentClient", "Confluence content: pages, blog posts, whiteboards, databases, folders.", "confluence", [
    M("get", "confluence.content.get", "ConfluenceContentReader", "get_content",
      'content_id: str, *, type: ContentType = ContentType.PAGE, body_format: BodyFormat = "markdown", version: int | None = None', "Content",
      "Fetch one piece of Confluence content by id.", "You have a page id or URL id.", 'page = await client.confluence.content.get("123456")',
      docs={"version": "Specific version number; default the current version."},
      raises="NotFoundError: The content does not exist OR you lack view permission."),
    M("list", "confluence.content.list", "ConfluenceContentReader", "list_content",
      '*, space_id: str | None = None, type: ContentType = ContentType.PAGE, status: str = "current", title: str | None = None, '
      "limit: int = 25, cursor: str | None = None", "Page[Content]", "List content, optionally in one space or by title.",
      "Browsing a space. For topic search use `confluence.search.cql`.", 'page = await client.confluence.content.list(space_id="12345")',
      docs={"space_id": "Numeric space id (resolve a key with `confluence.spaces.get`).", "status": "current, archived or draft.",
            "title": "Exact title filter."}),
    M("children", "confluence.content.children", "ConfluenceContentReader", "list_children",
      "content_id: str, *, type: ContentType = ContentType.PAGE, limit: int = 25, cursor: str | None = None", "Page[Content]",
      "Direct children of a page.", "Walking the page tree.", 'page = await client.confluence.content.children("123456")'),
    M("ancestors", "confluence.content.ancestors", "ConfluenceContentReader", "list_ancestors",
      "content_id: str, *, type: ContentType = ContentType.PAGE", "list[Content]", "Ancestors of a page, root first.", "Finding where a page lives.",
      'chain = await client.confluence.content.ancestors("123456")'),
    M("descendants", "confluence.content.descendants", "ConfluenceContentReader", "list_descendants",
      "content_id: str, *, depth: int | None = None, limit: int = 50, cursor: str | None = None", "Page[Content]", "All descendants of a page.",
      "Subtree questions.", 'page = await client.confluence.content.descendants("123456", depth=2)', docs={"depth": "Maximum depth; None for all."}),
    M("versions", "confluence.content.versions", "ConfluenceContentReader", "list_versions", "content_id: str, *, limit: int = 25, cursor: str | None = None",
      "Page[ContentVersion]", "Version history of a page.", "Auditing changes.", 'page = await client.confluence.content.versions("123456")'),
    M("diff", "confluence.content.diff", "ConfluenceContentReader", "diff_versions", "content_id: str, from_version: int, to_version: int", "ContentDiff",
      "Unified diff (Markdown) between two versions.", "Seeing what changed.", "d = await client.confluence.content.diff(\"123456\", 3, 4)",
      docs={"from_version": "Older version number.", "to_version": "Newer version number."}),
    M("resolve_macros", "confluence.content.macros.resolve", "ConfluenceContentReader", "resolve_macros", "content_id: str", "Content",
      "Page content with macros rendered (partial on REST).", "A page relies on macros such as include or Jira issues.",
      'page = await client.confluence.content.resolve_macros("123456")'),
])
cls("ConfluenceSearchClient", "Confluence CQL search.", "confluence", [
    M("cql", "confluence.search.cql", "ConfluenceSearcher", "search_cql", "cql: str, *, limit: int = 25, cursor: str | None = None, excerpt: bool = True",
      "Page[SearchHit]", "Search Confluence with CQL and return ONE page of hits (ids and excerpts, not bodies).",
      "Finding pages by topic, space, label or date. Then fetch bodies with `confluence.content.get`.",
      'page = await client.confluence.search.cql(\'space = "ENG" AND text ~ "threat model"\')', docs={"excerpt": "Include highlighted excerpts."},
      raises="InvalidRequestError: The CQL is empty or invalid."),
])
cls("ConfluenceSpacesClient", "Confluence spaces.", "confluence", [
    M("list", "confluence.space.list", "ConfluenceSpaceReader", "list_spaces",
      "*, keys: Sequence[str] = (), type: str | None = None, limit: int = 25, cursor: str | None = None", "Page[Space]", "List spaces you can see.",
      "You need a space key or id.", "page = await client.confluence.spaces.list()", docs={"keys": "Restrict to these space keys."}),
    M("get", "confluence.space.get", "ConfluenceSpaceReader", "get_space", "key_or_id: str", "Space", "Fetch one space by key or id.", "Resolving a space key to its id.",
      'sp = await client.confluence.spaces.get("ENG")'),
    M("personal", "confluence.space.personal", "ConfluenceSpaceReader", "get_personal_space", "account_id: str | None = None", "Space",
      "A user's personal space.", "Looking for someone's personal notes.", "sp = await client.confluence.spaces.personal()"),
    M("instructions", "confluence.space.instructions.get", "ConfluenceSpaceReader", "get_space_instructions", "key_or_id: str", "str | None",
      "Space-level agent instructions (MCP only; None on REST).", "A space defines how agents should behave.",
      'text = await client.confluence.spaces.instructions("ENG")'),
    M("permissions", "confluence.space.permissions.list", "ConfluenceSpaceReader", "list_space_permissions",
      "key_or_id: str, *, limit: int = 50, cursor: str | None = None", "Page[dict[str, Any]]", "Permissions of a space.", "Access reviews.",
      'page = await client.confluence.spaces.permissions("ENG")'),
])
cls("ConfluenceCommentsClient", "Confluence comments (footer and inline).", "confluence", [
    M("list", "confluence.comment.list", "ConfluenceContentReader", "list_comments",
      'content_id: str, *, kind: Literal["footer", "inline", "all"] = "all", limit: int = 25, cursor: str | None = None', "Page[ConfluenceComment]",
      "Comments on a page.", "Reading discussion.", 'page = await client.confluence.comments.list("123456")', docs={"kind": "footer, inline or all."}),
])
cls("ConfluenceAttachmentsClient", "Confluence attachments.", "confluence", [
    M("list", "confluence.attachment.list", "ConfluenceContentReader", "list_attachments", "content_id: str, *, limit: int = 25, cursor: str | None = None",
      "Page[Attachment]", "Attachment metadata of a page.", "Before downloading.", 'page = await client.confluence.attachments.list("123456")'),
    M("download", "confluence.attachment.download", "ConfluenceContentReader", "download_attachment",
      "attachment_id: str, *, dest: Path | None = None, max_bytes: int | None = None, extract_text: bool = False", "AttachmentContent",
      "Download one attachment.", "You need file content.", 'c = await client.confluence.attachments.download("att123", max_bytes=1_000_000)',
      docs={"attachment_id": "Attachment id from `list`."}),
])
cls("ConfluenceLabelsClient", "Confluence labels.", "confluence", [
    M("list", "confluence.label.list", "ConfluenceContentReader", "list_labels", "content_id: str", "list[Label]", "Labels on a page.", "Classification.",
      'ls = await client.confluence.labels.list("123456")'),
])
cls("ConfluencePropertiesClient", "Confluence content properties.", "confluence", [
    M("get", "confluence.property.get", "ConfluenceContentReader", "get_properties", "content_id: str", "dict[str, Any]", "All properties of a page.",
      "An app stored metadata.", 'p = await client.confluence.properties.get("123456")'),
])
cls("ConfluenceTasksClient", "Confluence inline tasks.", "confluence", [
    M("list", "confluence.task.list", "ConfluenceContentReader", "list_tasks",
      '*, space_id: str | None = None, content_id: str | None = None, status: Literal["complete", "incomplete"] | None = None, limit: int = 25, '
      "cursor: str | None = None", "Page[Task]", "Inline tasks in a space or page.", "Finding open action items.",
      'page = await client.confluence.tasks.list(status="incomplete")', docs={"status": "complete or incomplete."}),
])
cls("ConfluencePermissionsClient", "Confluence permissions and restrictions.", "confluence", [
    M("permissions", "confluence.permission.get", "ConfluenceContentReader", "get_permissions", "content_id: str", "Permissions",
      "What the current user may do with a page.", "Checking your own access.", 'p = await client.confluence.permissions.permissions("123456")'),
    M("restrictions", "confluence.permission.restrictions.get", "ConfluenceContentReader", "get_restrictions", "content_id: str", "Restrictions",
      "Read/update restrictions on a page.", "Mirroring permissions downstream.", 'r = await client.confluence.permissions.restrictions("123456")'),
])
cls("ConfluenceTemplatesClient", "Confluence page templates.", "confluence", [
    M("list", "confluence.template.list", "ConfluenceTemplateReader", "list_templates", "*, space_key: str | None = None", "list[Template]",
      "List page templates.", "Creating pages from templates.", 'ts = await client.confluence.templates.list(space_key="ENG")'),
    M("get", "confluence.template.get", "ConfluenceTemplateReader", "get_template", "template_id: str", "Template", "Fetch one template.",
      "You have a template id.", 'tpl = await client.confluence.templates.get("tpl1")', docs={"template_id": "Template id."}),
])
cls("GraphClient", "Teamwork Graph context and Rovo semantic search (Cloud only; consume Rovo credits).", "graph", [
    M("context", "graph.context", "TeamworkGraphReader", "get_context", "ari_or_url: str, *, max_items: int = 50", "GraphContext",
      "Connected context (related work, people, documents) for an Atlassian entity.",
      "The user asks about RELATIONSHIPS or context of an issue/page, not just its fields.",
      'ctx = await client.graph.context("https://example.atlassian.net/browse/PROJ-1")',
      docs={"ari_or_url": "Atlassian Resource Identifier or the entity URL.", "max_items": "Maximum related items."},
      raises="PolicyDeniedError: Rovo credit consumption is not enabled (allow_credit_consuming)."),
    M("objects", "graph.object.get", "TeamworkGraphReader", "get_objects", "aris_or_urls: Sequence[str]", "list[GraphObject]",
      "Fetch graph objects by ARI or URL.", "You have ARIs or URLs and need object details.",
      'objs = await client.graph.objects(["https://example.atlassian.net/browse/PROJ-1"])',
      docs={"aris_or_urls": "ARIs or URLs of the objects."}),
    M("semantic_search", "graph.search.semantic", "SemanticSearcher", "semantic_search", "query: str, *, products: Sequence[str] = (), limit: int = 20",
      "list[SemanticHit]", "Semantic search across Jira, Confluence and connected apps (Rovo).",
      "Fuzzy or cross-product questions where exact JQL/CQL is unknown.", 'hits = await client.graph.semantic_search("how do we rotate keys")',
      docs={"products": "Restrict to products, e.g. [\"confluence\"]."},
      raises="PolicyDeniedError: Rovo credit consumption is not enabled (allow_credit_consuming)."),
])
cls("RawClient", "Raw access to any REST endpoint with auth, retry and pagination applied.", "raw", [
    M("request", "raw.request", "RawRequester", "request",
      'method: str, path: str, *, product: Literal["jira", "confluence", "jsm", "agile", "admin"], params: Mapping[str, Any] | None = None, '
      "json: Any = None, headers: Mapping[str, str] | None = None", "RawResponse", "Call any endpoint of a product's REST API.",
      "No curated operation exists. Prefer curated operations when they do.",
      'r = await client.raw.request("GET", "/myself", product="jira")',
      docs={"method": "HTTP method.", "path": "Path relative to the product API root, e.g. /myself.", "product": "Which API family.",
            "params": "Query parameters.", "json": "JSON request body.", "headers": "Extra headers."}),
])


def extend(name: str, methods: list[M]) -> None:
    for entry in SPECS:
        if entry[0] == name:
            entry[3].extend(methods)
            return
    raise KeyError(name)


# ---------------------------------------------------------------- P2: writes (every method gets dry_run automatically)
extend("JiraIssuesClient", [
    M("create", "jira.issue.create", "JiraIssueWriter", "create_issue", "draft: IssueDraft", "CreatedIssue", "Create a Jira issue.",
      "The user asked you to create a ticket. Fetch valid issue types with `jira.metadata.issue_types` first.",
      'r = await client.jira.issues.create(IssueDraft(project="PROJ", issue_type="Bug", summary="Login fails"), dry_run=True)',
      docs={"draft": "IssueDraft: project key, issue type, summary, optional Markdown description, assignee, labels, parent, extra fields by NAME or id."}),
    M("bulk_create", "jira.issue.bulk_create", "JiraIssueWriter", "bulk_create_issues", "drafts: Sequence[IssueDraft]", "_List[CreatedIssue]",
      "Create up to 50 issues in one call.", "Creating many similar issues.", 'r = await client.jira.issues.bulk_create([IssueDraft(project="PROJ", issue_type="Task", summary="A")], dry_run=True)',
      docs={"drafts": "1 to 50 IssueDraft objects."}),
    M("edit", "jira.issue.edit", "JiraIssueWriter", "edit_issue", "key: str, update: IssueUpdate, *, notify_users: bool = True", "None",
      "Edit an issue's summary, description, labels or fields.", "Changing existing issue data. To change status use `transition`.",
      'r = await client.jira.issues.edit("PROJ-1", IssueUpdate(summary="New title"), dry_run=True)'),
    M("assign", "jira.issue.assign", "JiraIssueWriter", "assign_issue", "key: str, assignee: str | None", "None", "Assign an issue to a person (or unassign).",
      "The user asks to assign or unassign.", 'r = await client.jira.issues.assign("PROJ-1", "ann@example.com", dry_run=True)'),
    M("transition", "jira.issue.transition", "JiraIssueWriter", "transition_issue",
      "key: str, transition: str, *, fields: dict[str, Any] | None = None, comment: BodyInput | str | None = None", "None",
      "Move an issue through its workflow (e.g. to Done).", "The user asks to change an issue's status. List options with `transitions` first.",
      'r = await client.jira.issues.transition("PROJ-1", "Done", dry_run=True)',
      docs={"fields": "Fields required by the transition screen (names or ids).", "comment": "Optional Markdown comment added with the transition."}),
    M("add_comment", "jira.issue.comment.add", "JiraIssueWriter", "add_comment", "key: str, body: BodyInput | str, *, visibility: dict[str, str] | None = None",
      "Comment", "Add a comment to an issue.", "The user asks you to comment.", 'r = await client.jira.issues.add_comment("PROJ-1", "Looking into it", dry_run=True)',
      docs={"visibility": 'Restrict to a role/group, e.g. {"type": "role", "value": "Developers"}.'}),
    M("edit_comment", "jira.issue.comment.edit", "JiraIssueWriter", "edit_comment", "key: str, comment_id: str, body: BodyInput | str", "Comment",
      "Edit an existing comment.", "Correcting your own earlier comment.", 'r = await client.jira.issues.edit_comment("PROJ-1", "10000", "fixed", dry_run=True)'),
    M("add_worklog", "jira.issue.worklog.add", "JiraIssueWriter", "add_worklog",
      "key: str, time_spent: str, *, started: str | None = None, comment: BodyInput | str | None = None", "Worklog", "Log time on an issue.",
      "The user asks to log work.", 'r = await client.jira.issues.add_worklog("PROJ-1", "2h", dry_run=True)',
      docs={"time_spent": 'Jira duration, e.g. "2h 30m".', "started": "ISO-8601 start time; default now."}),
    M("link", "jira.issue.link.create", "JiraIssueWriter", "create_link", "link_type: str, inward_key: str, outward_key: str", "None",
      "Link two issues.", "The user asks to relate/block/duplicate issues.", 'r = await client.jira.issues.link("Blocks", "PROJ-1", "PROJ-2", dry_run=True)',
      docs={"inward_key": "Issue key on the inward side.", "outward_key": "Issue key on the outward side."}),
    M("add_remote_link", "jira.issue.remote_link.create", "JiraIssueWriter", "create_remote_link", "key: str, url: str, title: str", "RemoteLink",
      "Attach a web link to an issue.", "Referencing an external page.", 'r = await client.jira.issues.add_remote_link("PROJ-1", "https://example.com", "Doc", dry_run=True)'),
    M("upload_attachment", "jira.issue.attachment.upload", "JiraIssueWriter", "upload_attachment", "key: str, file: Path | bytes, *, filename: str | None = None",
      "_List[JiraAttachment]", "Upload a file to an issue.", "The user asks to attach a file.", 'r = await client.jira.issues.upload_attachment("PROJ-1", b"data", filename="a.txt", dry_run=True)'),
    M("watch", "jira.issue.watch", "JiraIssueWriter", "watch_issue", "key: str, *, watch: bool = True, account_id: str | None = None", "None",
      "Start or stop watching an issue.", "The user wants notifications for an issue.", 'r = await client.jira.issues.watch("PROJ-1", dry_run=True)',
      docs={"watch": "True to watch, False to stop."}),
    M("set_property", "jira.issue.property.set", "JiraIssueWriter", "set_property", "key: str, property_key: str, value: Any", "None",
      "Set an app-defined JSON property on an issue.", "Storing app metadata on an issue.", 'r = await client.jira.issues.set_property("PROJ-1", "my.app", {"a": 1}, dry_run=True)'),
    M("convert_hierarchy", "jira.issue.hierarchy.convert", "JiraIssueWriter", "convert_hierarchy", "key: str, *, new_issue_type: str, new_parent: str | None = None", "None",
      "Change an issue's hierarchy level (MCP only).", "Turning a task into a sub-task or epic.", 'r = await client.jira.issues.convert_hierarchy("PROJ-1", new_issue_type="Epic", dry_run=True)',
      docs={"new_issue_type": "Target issue type name.", "new_parent": "New parent key, if any."}),
    M("delete", "jira.issue.delete", "JiraIssueDeleter", "delete_issue", "key: str, *, delete_subtasks: bool = False", "None",
      "Permanently delete an issue.", "ONLY when the user explicitly asks to delete. Prefer transitioning to Done/Closed.",
      'r = await client.jira.issues.delete("PROJ-1", dry_run=True)', docs={"delete_subtasks": "Also delete sub-tasks."}),
    M("delete_comment", "jira.issue.comment.delete", "JiraIssueDeleter", "delete_comment", "key: str, comment_id: str", "None", "Delete a comment.",
      "The user asks to remove a comment.", 'r = await client.jira.issues.delete_comment("PROJ-1", "10000", dry_run=True)'),
    M("delete_attachment", "jira.issue.attachment.delete", "JiraIssueDeleter", "delete_attachment", "attachment_id: str", "None", "Delete an attachment.",
      "The user asks to remove a file.", 'r = await client.jira.issues.delete_attachment("att1", dry_run=True)'),
])
extend("JiraProjectsClient", [
    M("manage_version", "jira.project.version.manage", "JiraProjectWriter", "manage_version", "project: str, change: VersionChange", "Version",
      "Create, update, release, archive or delete a project version.", "Release management.",
      'r = await client.jira.projects.manage_version("PROJ", VersionChange(action="create", name="2.0"), dry_run=True)'),
    M("create", "jira.project.create", "JiraAdmin", "create_project", "key: str, name: str, *, template: str, lead: str", "Project",
      "Create a project (MANAGE access).", "The user asks for a new project and an admin enabled MANAGE.",
      'r = await client.jira.projects.create("NEW", "New project", template="com.pyxis.greenhopper.jira:gh-simplified-agility-scrum", lead="ann@example.com", dry_run=True)',
      docs={"template": "Project template key.", "lead": "Project lead (accountId, email or name)."}),
    M("update", "jira.project.update", "JiraAdmin", "update_project", "key: str, changes: dict[str, Any]", "Project", "Update project settings (MANAGE access).",
      "Renaming or reconfiguring a project.", 'r = await client.jira.projects.update("PROJ", {"name": "Renamed"}, dry_run=True)', docs={"changes": "Project fields to change."}, expand="changes"),
])
cls("JiraAdminClient", "Jira administration (MANAGE access; disabled by default).", "jira", [
    M("workflows", "jira.admin.workflows.list", "JiraAdmin", "list_workflows", "*, project: str | None = None", "_List[Workflow]", "List workflows.",
      "Understanding statuses and transitions.", "ws = await client.jira.admin.workflows()"),
    M("workflow_editor_link", "jira.admin.workflow.editor_link", "JiraAdmin", "get_workflow_editor_link", "workflow_id: str", "str",
      "URL of the workflow editor.", "The user wants to edit a workflow in the UI.", 'u = await client.jira.admin.workflow_editor_link("wf1")',
      docs={"workflow_id": "Workflow id."}),
    M("screen", "jira.admin.screen.get", "JiraAdmin", "get_screen", "screen_id: str", "Screen", "Fetch a screen.", "Inspecting field layout.",
      'sc = await client.jira.admin.screen("1")', docs={"screen_id": "Screen id."}),
    M("update_screen", "jira.admin.screen.update", "JiraAdmin", "update_screen", "screen_id: str, changes: dict[str, Any]", "Screen", "Update a screen (MANAGE access).",
      "Changing field layout.", 'r = await client.jira.admin.update_screen("1", {"name": "Renamed"}, dry_run=True)', docs={"screen_id": "Screen id.", "changes": "Screen fields to change."}, expand="changes"),
])
extend("JiraAgileClient", [
    M("manage_sprint", "jira.agile.sprint.manage", "JiraAgileWriter", "manage_sprint", "change: SprintChange", "Sprint",
      "Create, update, start, close or delete a sprint.", "Sprint planning.", 'r = await client.jira.agile.manage_sprint(SprintChange(action="create", board_id=42, name="S8"), dry_run=True)'),
    M("move_to_sprint", "jira.agile.issue.move_to_sprint", "JiraAgileWriter", "move_issues_to_sprint", "sprint_id: int, issue_keys: Sequence[str]", "None",
      "Move issues into a sprint.", "Sprint planning.", 'r = await client.jira.agile.move_to_sprint(7, ["PROJ-1"], dry_run=True)'),
    M("move_to_backlog", "jira.agile.issue.move_to_sprint", "JiraAgileWriter", "move_issues_to_backlog", "issue_keys: Sequence[str]", "None",
      "Move issues back to the backlog.", "Sprint planning.", 'r = await client.jira.agile.move_to_backlog(["PROJ-1"], dry_run=True)'),
    M("rank", "jira.agile.issue.rank", "JiraAgileWriter", "rank_issues", "issue_keys: Sequence[str], *, before: str | None = None, after: str | None = None", "None",
      "Rank issues before or after another issue.", "Backlog ordering.", 'r = await client.jira.agile.rank(["PROJ-1"], before="PROJ-2", dry_run=True)',
      docs={"before": "Rank before this issue key.", "after": "Rank after this issue key."}),
    M("create_board", "jira.agile.board.create", "JiraAgileWriter", "create_board", "name: str, *, filter_id: int, board_type: str = \"scrum\"", "Board",
      "Create a board.", "The user asks for a new board.", 'r = await client.jira.agile.create_board("Team", filter_id=10, dry_run=True)',
      docs={"filter_id": "Saved filter id the board is based on.", "board_type": "scrum or kanban."}),
])
extend("JsmClient", [
    M("create_request", "jsm.request.create", "JsmWriter", "create_request",
      "service_desk_id: str, request_type_id: str, fields: dict[str, Any], *, on_behalf_of: str | None = None", "CustomerRequest", "Raise a customer request.",
      "The user wants to open a support request.", 'r = await client.jsm.create_request("4", "r1", {"summary": "VPN"}, dry_run=True)',
      docs={"request_type_id": "Request type id.", "fields": "Request fields by field id.", "on_behalf_of": "accountId of the customer."}),
    M("add_comment", "jsm.request.comment.add", "JsmWriter", "add_request_comment", "key: str, body: BodyInput | str, *, public: bool", "RequestComment",
      "Reply to a request (public) or add an internal note.", "Responding to a customer. `public` MUST be chosen deliberately.",
      'r = await client.jsm.add_comment("OPS-1", "We are on it", public=True, dry_run=True)', docs={"public": "True = customer-visible reply; False = internal note."}),
    M("transition", "jsm.request.transition", "JsmWriter", "transition_request", "key: str, transition: str, *, comment: BodyInput | str | None = None", "None",
      "Move a request through its workflow.", "Resolving or reopening a request.", 'r = await client.jsm.transition("OPS-1", "Resolve", dry_run=True)',
      docs={"comment": "Optional comment."}),
    M("approve", "jsm.request.approve", "JsmWriter", "answer_approval", "key: str, approval_id: str, *, approve: bool", "None", "Approve or decline an approval step.",
      "The user is an approver and asks for a decision.", 'r = await client.jsm.approve("OPS-1", "a1", approve=True, dry_run=True)',
      docs={"approval_id": "Approval id.", "approve": "True to approve, False to decline."}),
    M("add_customers", "jsm.customer.add", "JsmWriter", "add_customers", "service_desk_id: str, account_ids: list[str]", "None", "Add customers to a service desk.",
      "Onboarding customers.", 'r = await client.jsm.add_customers("4", ["acc-1"], dry_run=True)', docs={"account_ids": "accountIds to add."}),
])
extend("ConfluenceContentClient", [
    M("create", "confluence.content.create", "ConfluenceContentWriter", "create_content", "draft: ContentDraft", "Content", "Create a page, blog post or folder.",
      "The user asks you to write a page.", 'r = await client.confluence.content.create(ContentDraft(title="Runbook", space="ENG", body="# Hi"), dry_run=True)',
      docs={"draft": "ContentDraft: title, space key/id, Markdown body, optional parent_id, type (page/blogpost/folder)."}),
    M("update", "confluence.content.update", "ConfluenceContentWriter", "update_content", "content_id: str, update: ContentUpdate", "Content",
      "Update a page's title/body. Version-checked: fails with ConflictError if the page changed since you read it.",
      "Editing an existing page. Fetch it first and pass its version.", 'r = await client.confluence.content.update("123", ContentUpdate(body="new", version=3), dry_run=True)',
      raises="ConflictError: The page changed since you read it; re-fetch and retry."),
    M("move", "confluence.content.move", "ConfluenceContentWriter", "move_content",
      'content_id: str, *, target_parent_id: str | None = None, target_space: str | None = None, position: str = "append"', "Content", "Move a page under another page.",
      "Reorganizing a space.", 'r = await client.confluence.content.move("123", target_parent_id="100", dry_run=True)',
      docs={"target_parent_id": "New parent page id.", "target_space": "New space key.", "position": "append, before or after."}),
    M("copy", "confluence.content.copy", "ConfluenceContentWriter", "copy_content", "content_id: str, *, destination_parent_id: str, title: str | None = None", "Content",
      "Copy a page.", "Templates or duplication.", 'r = await client.confluence.content.copy("123", destination_parent_id="100", dry_run=True)',
      docs={"destination_parent_id": "Parent page of the copy."}),
    M("archive", "confluence.content.archive", "ConfluenceContentWriter", "archive_content", "content_ids: Sequence[str]", "None", "Archive pages.",
      "Retiring pages without deleting them (preferred over delete).", 'r = await client.confluence.content.archive(["123"], dry_run=True)'),
    M("unarchive", "confluence.content.unarchive", "ConfluenceContentWriter", "unarchive_content", "content_ids: Sequence[str]", "None", "Restore archived pages (MCP only).",
      "Undoing an archive.", 'r = await client.confluence.content.unarchive(["123"], dry_run=True)'),
    M("restore_version", "confluence.content.restore_version", "ConfluenceContentWriter", "restore_version", "content_id: str, version: int, *, message: str | None = None", "Content",
      "Restore an older version of a page as the newest version.", "Reverting a bad edit.", 'r = await client.confluence.content.restore_version("123", 2, dry_run=True)',
      docs={"version": "Version number to restore.", "message": "Version message."}),
    M("set_status", "confluence.content.set_status", "ConfluenceContentWriter", "set_status", "content_id: str, status: str", "None", "Set a content status (MCP only).",
      "Workflow states on pages.", 'r = await client.confluence.content.set_status("123", "Ready", dry_run=True)', docs={"status": "Status name."}),
    M("delete", "confluence.content.delete", "ConfluenceContentDeleter", "delete_content", "content_id: str, *, type: ContentType = ContentType.PAGE, purge: bool = False", "None",
      "Delete a page (moves it to trash unless purge=True).", "ONLY when the user explicitly asks. Prefer `archive`.", 'r = await client.confluence.content.delete("123", dry_run=True)'),
])
extend("ConfluenceCommentsClient", [
    M("create", "confluence.comment.create", "ConfluenceContentWriter", "add_comment",
      "content_id: str, body: BodyInput | str, *, parent_comment_id: str | None = None, inline_anchor: dict[str, Any] | None = None", "ConfluenceComment",
      "Comment on a page (footer) or on a text selection (inline).", "The user asks you to comment.", 'r = await client.confluence.comments.create("123", "Thanks", dry_run=True)',
      docs={"parent_comment_id": "Reply to this comment.", "inline_anchor": "Anchor for an inline comment (text selection)."}),
    M("update", "confluence.comment.update", "ConfluenceContentWriter", "update_comment", "comment_id: str, body: BodyInput | str", "ConfluenceComment", "Edit a comment.",
      "Correcting a comment.", 'r = await client.confluence.comments.update("fc1", "edited", dry_run=True)'),
    M("resolve", "confluence.comment.resolve", "ConfluenceContentWriter", "resolve_comment", "comment_id: str, *, resolved: bool = True", "None", "Resolve or reopen an inline comment.",
      "Closing a review thread.", 'r = await client.confluence.comments.resolve("ic1", dry_run=True)', docs={"resolved": "True to resolve, False to reopen."}),
    M("delete", "confluence.comment.delete", "ConfluenceContentDeleter", "delete_comment", "comment_id: str", "None", "Delete a comment.", "The user asks to remove a comment.",
      'r = await client.confluence.comments.delete("fc1", dry_run=True)'),
])
extend("ConfluenceAttachmentsClient", [
    M("upload", "confluence.attachment.upload", "ConfluenceContentWriter", "upload_attachment", "content_id: str, file: Path | bytes, *, filename: str | None = None, comment: str | None = None",
      "Attachment", "Upload a file to a page.", "The user asks to attach a file.", 'r = await client.confluence.attachments.upload("123", b"x", filename="a.txt", dry_run=True)',
      docs={"comment": "Version comment."}),
    M("delete", "confluence.attachment.delete", "ConfluenceContentDeleter", "delete_attachment", "attachment_id: str, *, purge: bool = False", "None", "Delete an attachment.",
      "The user asks to remove a file.", 'r = await client.confluence.attachments.delete("cat1", dry_run=True)'),
])
extend("ConfluenceLabelsClient", [
    M("add", "confluence.label.add", "ConfluenceContentWriter", "add_labels", "content_id: str, labels: Sequence[str]", "_List[Label]", "Add labels to a page.", "Classification.",
      'r = await client.confluence.labels.add("123", ["runbook"], dry_run=True)'),
    M("remove", "confluence.label.remove", "ConfluenceContentWriter", "remove_label", "content_id: str, label: str", "None", "Remove a label from a page.", "Reclassification.",
      'r = await client.confluence.labels.remove("123", "runbook", dry_run=True)'),
])
extend("ConfluencePropertiesClient", [
    M("set", "confluence.property.set", "ConfluenceContentWriter", "set_property", "content_id: str, key: str, value: Any", "None", "Set a content property.", "Storing app metadata.",
      'r = await client.confluence.properties.set("123", "app.state", {"ok": True}, dry_run=True)'),
])
extend("ConfluenceTasksClient", [
    M("set_status", "confluence.task.complete", "ConfluenceContentWriter", "set_task_status", "task_id: str, *, complete: bool", "Task", "Complete or reopen an inline task.",
      "The user ticks off an action item.", 'r = await client.confluence.tasks.set_status("t1", complete=True, dry_run=True)', docs={"task_id": "Task id.", "complete": "True to complete."}),
])
extend("ConfluencePermissionsClient", [
    M("set_restrictions", "confluence.permission.restrictions.set", "ConfluenceContentWriter", "set_restrictions", "content_id: str, restrictions: Restrictions", "None",
      "Replace the read/update restrictions of a page.", "Locking down a page.", 'r = await client.confluence.permissions.set_restrictions("123", Restrictions(read_groups=["eng"]), dry_run=True)',
      docs={"restrictions": "The full restriction set to apply."}),
    M("add", "confluence.permission.add", "ConfluencePermissionWriter", "add_permissions", "content_id: str, grants: Sequence[dict[str, Any]]", "None", "Add restrictions.",
      "Granting access to a restricted page.", 'r = await client.confluence.permissions.add("123", [{"operation": "read", "group": "eng"}], dry_run=True)'),
    M("remove", "confluence.permission.remove", "ConfluencePermissionWriter", "remove_permissions", "content_id: str, grants: Sequence[dict[str, Any]]", "None", "Remove restrictions.",
      "Revoking access.", 'r = await client.confluence.permissions.remove("123", [{"operation": "read", "group": "eng"}], dry_run=True)'),
    M("replace", "confluence.permission.replace", "ConfluencePermissionWriter", "replace_permissions", "content_id: str, grants: Sequence[dict[str, Any]]", "None", "Replace all restrictions.",
      "Resetting access.", 'r = await client.confluence.permissions.replace("123", [{"operation": "read", "group": "eng"}], dry_run=True)'),
    M("set_public_link", "confluence.content.public_link.set", "ConfluencePermissionWriter", "set_public_link", "content_id: str, *, enabled: bool", "None",
      "Enable or disable the public link of a page (MCP only).", "Sharing outside the org (be careful).", 'r = await client.confluence.permissions.set_public_link("123", enabled=False, dry_run=True)'),
])
extend("ConfluenceSpacesClient", [
    M("create", "confluence.space.create", "ConfluenceSpaceWriter", "create_space", "key: str, name: str, *, description: str | None = None", "Space", "Create a space.",
      "The user asks for a new space.", 'r = await client.confluence.spaces.create("NEW", "New space", dry_run=True)', docs={"description": "Plain-text description."}),
    M("set_instructions", "confluence.space.instructions.set", "ConfluenceSpaceWriter", "set_space_instructions", "key_or_id: str, instructions: str", "None",
      "Set agent instructions for a space (MCP only).", "Defining how agents should treat a space.", 'r = await client.confluence.spaces.set_instructions("ENG", "Be concise.", dry_run=True)',
      docs={"instructions": "Instruction text."}),
])
extend("GraphClient", [
    M("add_context", "graph.context.add", "TeamworkGraphWriter", "add_context", "ari_or_url: str, context: Mapping[str, Any]", "None", "Attach context to a graph entity (MCP only).",
      "Recording relationships for later retrieval.", 'r = await client.graph.add_context("ari:cloud:jira::issue/10001", {"note": "x"}, dry_run=True)', docs={"context": "Context key/values."}),
])


cls("ConfluenceSocialClient", "Watching, starring and reacting on Confluence content.", "confluence", [
    M("watch", "confluence.watch.content", "ConfluenceContentWriter", "watch",
      "*, content_id: str | None = None, space_key: str | None = None, label: str | None = None, watch: bool = True", "None",
      "Watch or unwatch a page, a space or a label.", "The user wants notifications.", 'r = await client.confluence.social.watch(content_id="123", dry_run=True)',
      docs={"watch": "True to watch, False to stop."}),
    M("star", "confluence.star.content", "ConfluenceContentWriter", "star", "*, content_id: str | None = None, space_key: str | None = None, star: bool = True", "None",
      "Favourite or unfavourite a page or space (MCP only).", "The user wants a bookmark.", 'r = await client.confluence.social.star(content_id="123", dry_run=True)',
      docs={"star": "True to star, False to unstar."}),
    M("react", "confluence.reaction.add", "ConfluenceContentWriter", "add_reaction", "content_id: str, emoji: str", "None", "React to a page with an emoji (MCP only).",
      "The user wants to acknowledge a page.", 'r = await client.confluence.social.react("123", "thumbsup", dry_run=True)'),
])

cls("ConfluenceSpecialClient", "MCP-only Confluence features: AI infographics and MAUI apps (Cloud; agent-interface).", "confluence", [
    M("get_maui_app", "confluence.special.maui_app.get", "ConfluenceSpecialContent", "get_maui_app", "app_id: str", "dict[str, Any]", "Fetch a MAUI app definition.",
      "Inspecting an existing MAUI app.", 'r = await client.confluence.special.get_maui_app("app1")', docs={"app_id": "MAUI app id."}),
    M("create_infographic", "confluence.special.infographic.create", "ConfluenceSpecialContent", "create_infographic", "content_id: str, *, prompt: str", "dict[str, Any]",
      "Generate an infographic for a page.", "The user asks for a visual summary of a page.", 'r = await client.confluence.special.create_infographic("123", prompt="Summarize", dry_run=True)',
      docs={"prompt": "What the infographic should show."}),
    M("edit_infographic", "confluence.special.infographic.edit", "ConfluenceSpecialContent", "edit_infographic", "content_id: str, infographic_id: str, *, prompt: str",
      "dict[str, Any]", "Revise an infographic.", "Iterating on a generated infographic.", 'r = await client.confluence.special.edit_infographic("123", "inf1", prompt="Add totals", dry_run=True)',
      docs={"infographic_id": "Infographic id."}),
    M("create_maui_app", "confluence.special.maui_app.create", "ConfluenceSpecialContent", "create_maui_app", "content_id: str, *, spec: str", "dict[str, Any]",
      "Create a MAUI app on a page.", "The user asks for an interactive app embedded in a page.", 'r = await client.confluence.special.create_maui_app("123", spec="counter", dry_run=True)',
      docs={"spec": "Description of the app to build."}),
    M("edit_maui_app", "confluence.special.maui_app.edit", "ConfluenceSpecialContent", "edit_maui_app", "app_id: str, *, spec: str", "dict[str, Any]", "Revise a MAUI app.",
      "Iterating on an app.", 'r = await client.confluence.special.edit_maui_app("app1", spec="add reset", dry_run=True)', docs={"app_id": "MAUI app id."}),
])

TYPE_DOCS = {"Page": "Page", "list": "list"}


MISSING: set[str] = set()


def _param_docs(sig: str, extra: dict[str, str], is_write: bool = False) -> list[str]:
    dry = "dry_run: bool = False, " if is_write else ""
    tree = ast.parse(f"def f({sig + (', ' if sig and not sig.rstrip().endswith('*') else '')}{'*, ' if '*' not in sig else ''}{dry}backend: BackendKind | str | None = None): pass")
    fn = tree.body[0]
    assert isinstance(fn, ast.FunctionDef)
    names = [a.arg for a in fn.args.args + fn.args.kwonlyargs]
    lines = []
    for nme in names:
        doc = extra.get(nme) or COMMON.get(nme)
        if not doc:
            MISSING.add(nme)
            doc = "?"
        lines.append(f"{nme}: {doc}")
    return lines


WRITE_ACCESS = {"WRITE", "DELETE", "MANAGE"}


def gen_method(m: M) -> str:
    op = ops.get(m.op)
    is_write = op.access.name in WRITE_ACCESS
    full_sig = m.sig
    if "*" not in full_sig:
        full_sig = (full_sig + ", " if full_sig else "") + "*"
    sig = f"{full_sig}, " + ("dry_run: bool = False, " if is_write else "") + "backend: BackendKind | str | None = None"
    tree = ast.parse(f"def f({sig}): pass")
    fn = tree.body[0]
    assert isinstance(fn, ast.FunctionDef)
    pos = [a.arg for a in fn.args.args if a.arg != m.expand]
    kw = [a.arg for a in fn.args.kwonlyargs if a.arg != "backend"]
    call_args = ", ".join(pos)
    call_kw = ", ".join(([f"**{m.expand}"] if m.expand else []) + [f"{k}={k}" for k in kw + ["backend"]])
    args_doc = "\n".join(f"    {line}" for line in _param_docs(m.sig, m.docs, is_write))
    doc = f"""{m.summary}

    When to use:
        {m.when}

    Args:
{textwrap.indent(args_doc, '    ')}

    Returns:
        {m.ret}

    Raises:
        {m.raises}{chr(10) + "        PolicyDeniedError: Write access is not enabled. ConfirmationDeclinedError: The user declined." if is_write else ""}

    Access: {op.access.name}{" (changes data; the user confirms; preview with dry_run=True)" if is_write else ""}

    Example:
        >>> {m.example}
    """
    doc = textwrap.indent(textwrap.dedent(doc).rstrip(), "        ")
    wrapped = []
    for line in doc.split("\n"):
        wrapped.append(line)
    ret = (m.ret + " | PlannedAction" if is_write else m.ret).replace("list[", "_List[")
    sig = sig.replace("list[", "_List[")
    return f'''    async def {m.name}(self, {sig}) -> {ret}:
        """{doc.lstrip()}
        """
        return cast("{ret}", await self._call("{m.op}", {m.proto}, "{m.method}", {call_args + ", " if call_args else ""}{call_kw}))
'''


def generate() -> None:
    by_file: dict[str, list[str]] = {}
    for name, doc, group, methods in SPECS:
        opmap = ", ".join(f'"{m.op}": "{m.name}"' for m in methods)
        body = [f'class {name}(Namespace):\n    """{doc}"""\n\n    _OPS = {{{opmap}}}\n']
        body += [gen_method(m) for m in methods]
        by_file.setdefault(group, []).append("\n".join(body))
    header = '''"""GENERATED by tools/gen_facade.py - edit the specs there, not this file."""
# ruff: noqa
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, cast

from ..capabilities import BackendKind
from ..models.common import BodyInput, Page, UserRef
from ..policy import PlannedAction
from ..models.confluence import (ContentDraft, ContentUpdate, Attachment, AttachmentContent, ConfluenceComment, Content, ContentDiff, ContentVersion, Label,
                                 Permissions, Restrictions, SearchHit, Space, Task, Template)
from ..models.jira import (CreatedIssue, IssueDraft, IssueUpdate, SprintChange, VersionChange, Workflow, Screen, Board, BoardConfig, ChangelogEntry, Comment, Component, Dashboard, Epic, Field, Filter, Issue, IssueLink,
                           IssueType, JqlParseResult, Project, RemoteLink, ServerInfo, Sprint, Status, Transition, Version, Worklog)
from ..models.jira import Attachment as JiraAttachment
from ..models.jira import AttachmentContent as JiraAttachmentContent
from ..models.jsm import CustomerRequest, JsmInfo, KbArticle, Organization, RequestComment, RequestType, ServiceDesk, SlaInfo
from ..models.graph import GraphContext, GraphObject, SemanticHit
from ..models.webhooks import RawResponse
from ..protocols import (ConfluenceContentReader, ConfluenceSearcher, ConfluenceSpaceReader, ConfluenceTemplateReader, JiraAgileReader,
                         JiraIssueReader, JiraIssueSearcher, JiraJqlTools, JiraMetadataReader, JiraProjectReader, JiraUserDirectory,
                         JsmReader, RawRequester, SemanticSearcher, TeamworkGraphReader, JiraIssueWriter, JiraIssueDeleter, JiraProjectWriter,
                         JiraAdmin, JiraAgileWriter, JsmWriter, ConfluenceContentWriter, ConfluenceSpaceWriter, ConfluencePermissionWriter,
                         ConfluenceContentDeleter, TeamworkGraphWriter, ConfluenceSpecialContent)
from ..protocols.confluence import BodyFormat, ContentType
from ._namespace import Namespace

_List = list


'''
    for group, chunks in by_file.items():
        text = header + "\n\n".join(chunks)
        if group == "jira" or group == "agile":
            pass
        (OUT / f"_generated_{group}.py").write_text(text + "\n", encoding="utf-8")
    if MISSING:
        raise SystemExit(f"missing parameter docs for: {sorted(MISSING)}")
    print("generated", ", ".join(f"_generated_{g}.py" for g in by_file))


if __name__ == "__main__":
    generate()
