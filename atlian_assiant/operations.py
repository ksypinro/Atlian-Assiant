"""Canonical operation catalog: the single source of truth for routing, policy, tools, docs and the support matrix.

Every task the client can perform has one stable id such as ``jira.issue.get``. Ids are a public API.
"""
from __future__ import annotations

from collections.abc import Iterable

from .capabilities import Access, Operation, Product

J, JS, SM, C, P = Product.JIRA, Product.JIRA_SOFTWARE, Product.JSM, Product.CONFLUENCE, Product.PLATFORM

_CATALOG: dict[str, Operation] = {}


def _add(
    prefix: str,
    product: Product,
    access: Access,
    names: Iterable[str],
    *,
    idempotent: bool = True,
    credits: bool = False,
    cloud_only: bool = False,
    tags: Iterable[str] = (),
) -> None:
    for name in names:
        op_id = f"{prefix}.{name}"
        if op_id in _CATALOG:
            raise ValueError(f"duplicate operation id {op_id}")
        summary = f"{access.value.title()}: {op_id.replace('.', ' ').replace('_', ' ')}"
        _CATALOG[op_id] = Operation(
            op_id, product, access, summary,
            idempotent=idempotent, consumes_credits=credits, cloud_only=cloud_only, tags=frozenset(tags),
        )


def _ns(prefix: str, product: Product, *, read: Iterable[str] = (), search: Iterable[str] = (),
        write: Iterable[str] = (), delete: Iterable[str] = (), manage: Iterable[str] = (),
        create: Iterable[str] = (), cloud_only: bool = False, credits: bool = False) -> None:
    """Register a namespace. ``create`` ids are WRITE and non-idempotent; ``write`` ids are WRITE and idempotent."""
    _add(prefix, product, Access.READ, read, cloud_only=cloud_only, credits=credits)
    _add(prefix, product, Access.SEARCH, search, cloud_only=cloud_only, credits=credits)
    _add(prefix, product, Access.WRITE, write, cloud_only=cloud_only)
    _add(prefix, product, Access.WRITE, create, idempotent=False, cloud_only=cloud_only)
    _add(prefix, product, Access.DELETE, delete, cloud_only=cloud_only)
    _add(prefix, product, Access.MANAGE, manage, cloud_only=cloud_only)


# ---- Jira ---------------------------------------------------------------------------------------------------------
_ns("jira.issue", J,
    read=["get", "comments.list", "changelog.list", "worklogs.list", "transitions.list", "links.list",
          "remote_links.list", "attachments.list", "attachment.download", "watchers.list", "property.get"],
    search=["search", "count"],
    write=["edit", "assign", "comment.edit", "watch", "property.set", "hierarchy.convert", "archive"],
    create=["create", "bulk_create", "transition", "comment.add", "worklog.add", "link.create", "remote_link.create",
            "attachment.upload"],
    delete=["delete", "comment.delete", "attachment.delete"])
_ns("jira.project", J, read=["list", "get", "components.list", "versions.list", "version.related_work"],
    write=["version.manage"], manage=["create", "update"])
_ns("jira.meta", J, read=["fields.list", "issue_types.list", "create_fields", "statuses.list", "priorities.list",
                          "resolutions.list", "link_types.list", "server_info"])
_ns("jira.user", J, read=["myself", "get", "search", "lookup_account_id", "assignable"])
_ns("jira.filter", J, read=["list", "get"])
_ns("jira.dashboard", J, read=["list", "get"])
_ns("jira.jql", J, read=["parse", "sanitize", "autocomplete", "suggest", "match", "pdcleaner"])
_ns("jira.expression", J, read=["evaluate"])
_ns("jira.admin", J, read=["workflows.list", "workflow.get", "workflow.editor_link", "screen.get"],
    manage=["screen.update"])
_ns("jira.agile", JS, read=["board.list", "board.get", "board.config", "board.issues", "sprint.list", "sprint.get",
                            "sprint.issues", "backlog.list", "epic.list"],
    write=["sprint.manage", "issue.rank", "issue.move_to_sprint"], create=["board.create"])
# ---- JSM ----------------------------------------------------------------------------------------------------------
_ns("jsm", SM, read=["info", "service_desk.list", "request_type.list", "request.list", "request.get",
                     "request.comments", "request.sla", "organization.list", "customer.list"],
    search=["kb.search"], write=["request.transition", "request.approve", "customer.add"],
    create=["request.create", "request.comment.add"])
# ---- Confluence ---------------------------------------------------------------------------------------------------
_ns("confluence.content", C,
    read=["get", "list", "children", "ancestors", "descendants", "versions", "version.get", "diff", "macros.resolve",
          "export", "public_link.get"],
    write=["update", "move", "archive", "unarchive", "restore_version", "set_status", "convert_mode",
           "public_link.set"],
    create=["create", "copy"], delete=["delete"])
_ns("confluence.comment", C, read=["list", "get"], write=["update", "resolve"], create=["create"], delete=["delete"])
_ns("confluence.attachment", C, read=["list", "get", "download"], create=["upload"], delete=["delete"])
_ns("confluence.label", C, read=["list"], write=["add", "remove"])
_ns("confluence.property", C, read=["get"], write=["set"])
_ns("confluence.task", C, read=["list", "get"], write=["complete", "reopen"])
_ns("confluence.reaction", C, read=["list"], write=["add"])
_ns("confluence.watch", C, write=["content", "space", "label"])
_ns("confluence.star", C, write=["content", "space"])
_ns("confluence.permission", C, read=["get", "restrictions.get"], write=["restrictions.set", "add", "remove", "replace"])
_ns("confluence.space", C, read=["list", "get", "personal", "instructions.get", "permissions.list"],
    write=["instructions.set"], create=["create"])
_ns("confluence.search", C, search=["cql"])
_ns("confluence.template", C, read=["list", "get"])
_ns("confluence.special", C, read=["maui_app.get"], write=["infographic.edit", "maui_app.edit"],
    create=["infographic.create", "maui_app.create"], cloud_only=True)
# ---- Platform -----------------------------------------------------------------------------------------------------
_ns("graph", P, read=["context", "object.get"], write=["context.add"], cloud_only=True, credits=True)
_CATALOG["graph.search.semantic"] = Operation("graph.search.semantic", P, Access.SEARCH, "Rovo semantic search",
                                              consumes_credits=True, cloud_only=True)
_CATALOG["graph.graphql.execute"] = Operation("graph.graphql.execute", P, Access.READ, "Execute a GraphQL query",
                                              cloud_only=True)
_add("analytics", P, Access.ANALYTICS, ["share.list", "table.list", "table.load", "table.query"], cloud_only=True)
_add("export", P, Access.READ, ["jira.csv", "confluence.page", "confluence.space"])
_add("automation", P, Access.WRITE, ["incoming_webhook.trigger"], idempotent=False)
_add("admin.audit", P, Access.ORG_ADMIN, ["list", "get", "actions", "stream"], cloud_only=True)
_add("webhook", P, Access.MANAGE, [
    "jira.dynamic.list", "jira.dynamic.register", "jira.dynamic.delete", "jira.dynamic.refresh", "jira.dynamic.failed",
    "jira.admin.list", "jira.admin.register", "jira.admin.update", "jira.admin.delete",
    "confluence_dc.list", "confluence_dc.register", "confluence_dc.update", "confluence_dc.delete",
    "confluence_dc.test", "confluence_dc.stats"])
_add("identity", P, Access.READ, ["whoami", "accessible_resources"])
_add("raw", P, Access.READ, ["request", "paginate", "generated.call"])

# Well-known aliases with richer summaries used by docs and tools
_SUMMARIES = {
    "jira.issue.get": "Fetch one issue by key or id",
    "jira.issue.search": "Search issues with JQL",
    "jira.issue.count": "Approximate JQL match count",
    "jira.issue.create": "Create an issue",
    "jira.issue.transition": "Move an issue through its workflow",
    "jira.issue.delete": "Delete an issue",
    "jira.project.create": "Create a project",
    "jira.agile.sprint.list": "List sprints of a board",
    "jsm.request.sla": "SLA status of a request",
    "confluence.content.get": "Fetch a page, blog post, whiteboard, database or folder by id",
    "confluence.search.cql": "Search content with CQL",
    "graph.context": "Teamwork Graph context for an entity",
    "analytics.table.load": "Load a Data Lake table",
    "admin.audit.stream": "Poll the organization audit events-stream",
}
for _k, _v in _SUMMARIES.items():
    _o = _CATALOG[_k]
    _CATALOG[_k] = Operation(_o.id, _o.product, _o.access, _v, _o.idempotent, _o.consumes_credits, _o.cloud_only, _o.tags)

JIRA_ISSUE_GET = _CATALOG["jira.issue.get"]
JIRA_ISSUE_SEARCH = _CATALOG["jira.issue.search"]
JIRA_ISSUE_COUNT = _CATALOG["jira.issue.count"]
JIRA_ISSUE_CREATE = _CATALOG["jira.issue.create"]
JIRA_ISSUE_TRANSITION = _CATALOG["jira.issue.transition"]
JIRA_ISSUE_DELETE = _CATALOG["jira.issue.delete"]
JIRA_PROJECT_CREATE = _CATALOG["jira.project.create"]
JIRA_SPRINT_LIST = _CATALOG["jira.agile.sprint.list"]
JSM_REQUEST_SLA = _CATALOG["jsm.request.sla"]
CONF_CONTENT_GET = _CATALOG["confluence.content.get"]
CONF_SEARCH_CQL = _CATALOG["confluence.search.cql"]
GRAPH_CONTEXT = _CATALOG["graph.context"]
GRAPH_SEMANTIC_SEARCH = _CATALOG["graph.search.semantic"]
ANALYTICS_LOAD_TABLE = _CATALOG["analytics.table.load"]
ORG_EVENTS_STREAM = _CATALOG["admin.audit.stream"]

CATALOG: dict[str, Operation] = dict(sorted(_CATALOG.items()))


def get(op_id: str) -> Operation:
    """Return the Operation for ``op_id`` or raise KeyError listing close matches."""
    try:
        return CATALOG[op_id]
    except KeyError:
        near = [k for k in CATALOG if k.startswith(op_id.rsplit(".", 1)[0])][:5]
        raise KeyError(f"unknown operation {op_id!r}; similar: {near}") from None


def by_prefix(prefix: str) -> list[Operation]:
    return [o for k, o in CATALOG.items() if k.startswith(prefix)]


def by_access(access: Access) -> list[Operation]:
    return [o for o in CATALOG.values() if o.access is access]
