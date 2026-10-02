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

TYPE_DOCS = {"Page": "Page", "list": "list"}


def _param_docs(sig: str, extra: dict[str, str]) -> list[str]:
    tree = ast.parse(f"def f({sig + (', ' if sig and not sig.rstrip().endswith('*') else '')}{'*, ' if '*' not in sig else ''}backend: BackendKind | str | None = None): pass")
    fn = tree.body[0]
    assert isinstance(fn, ast.FunctionDef)
    names = [a.arg for a in fn.args.args + fn.args.kwonlyargs]
    lines = []
    for nme in names:
        doc = extra.get(nme) or COMMON.get(nme)
        assert doc, f"no doc for parameter {nme!r}"
        lines.append(f"{nme}: {doc}")
    return lines


def gen_method(m: M) -> str:
    op = ops.get(m.op)
    full_sig = m.sig
    if "*" not in full_sig:
        full_sig = (full_sig + ", " if full_sig else "") + "*"
    sig = f"{full_sig}, backend: BackendKind | str | None = None"
    tree = ast.parse(f"def f({sig}): pass")
    fn = tree.body[0]
    assert isinstance(fn, ast.FunctionDef)
    pos = [a.arg for a in fn.args.args]
    kw = [a.arg for a in fn.args.kwonlyargs if a.arg != "backend"]
    call_args = ", ".join(pos)
    call_kw = ", ".join(f"{k}={k}" for k in kw + ["backend"])
    args_doc = "\n".join(f"    {line}" for line in _param_docs(m.sig, m.docs))
    doc = f"""{m.summary}

    When to use:
        {m.when}

    Args:
{textwrap.indent(args_doc, '    ')}

    Returns:
        {m.ret}

    Raises:
        {m.raises}

    Access: {op.access.name}

    Example:
        >>> {m.example}
    """
    doc = textwrap.indent(textwrap.dedent(doc).rstrip(), "        ")
    wrapped = []
    for line in doc.split("\n"):
        wrapped.append(line)
    ret = m.ret.replace("list[", "_List[")
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
from ..models.common import Page, UserRef
from ..models.confluence import (Attachment, AttachmentContent, ConfluenceComment, Content, ContentDiff, ContentVersion, Label,
                                 Permissions, Restrictions, SearchHit, Space, Task, Template)
from ..models.jira import (Board, BoardConfig, ChangelogEntry, Comment, Component, Dashboard, Epic, Field, Filter, Issue, IssueLink,
                           IssueType, JqlParseResult, Project, RemoteLink, ServerInfo, Sprint, Status, Transition, Version, Worklog)
from ..models.jira import Attachment as JiraAttachment
from ..models.jira import AttachmentContent as JiraAttachmentContent
from ..models.jsm import CustomerRequest, JsmInfo, KbArticle, Organization, RequestComment, RequestType, ServiceDesk, SlaInfo
from ..models.graph import GraphContext, GraphObject, SemanticHit
from ..models.webhooks import RawResponse
from ..protocols import (ConfluenceContentReader, ConfluenceSearcher, ConfluenceSpaceReader, ConfluenceTemplateReader, JiraAgileReader,
                         JiraIssueReader, JiraIssueSearcher, JiraJqlTools, JiraMetadataReader, JiraProjectReader, JiraUserDirectory,
                         JsmReader, RawRequester, SemanticSearcher, TeamworkGraphReader)
from ..protocols.confluence import BodyFormat, ContentType
from ._namespace import Namespace

_List = list


'''
    for group, chunks in by_file.items():
        text = header + "\n\n".join(chunks)
        if group == "jira" or group == "agile":
            pass
        (OUT / f"_generated_{group}.py").write_text(text + "\n", encoding="utf-8")
    print("generated", ", ".join(f"_generated_{g}.py" for g in by_file))


if __name__ == "__main__":
    generate()
