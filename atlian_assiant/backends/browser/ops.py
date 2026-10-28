"""Bindings from canonical operations to UI pages and UI flows (design 10.8).

Reads open a CANONICAL URL (``/browse/{key}``, ``/issues/?jql=...``, ``/wiki/spaces/-/pages/{id}``) and extract with the page spec of the same name in
``selectors.yaml``. Writes are explicit click flows and need ``allow_browser_writes=True`` plus confirmation. Nothing here calls a private XHR or GraphQL endpoint.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from ...core.errors import UnsupportedOperationError
from ...core.http import RequestSpec
from ..cli.commandmap import _leaves, shape

Vars = Mapping[str, str]
Step = tuple[Any, ...]                      # ("fill", selector, text) | ("click", selector) | ("attach", selector, name, bytes)


def _dig(d: Any, *keys: str) -> Any:
    for k in keys:
        d = d.get(k) if isinstance(d, Mapping) else None
    return d


@dataclass(frozen=True)
class Read:
    op: str
    path: str                                 # REST path template this binding answers
    url: str                                  # UI path template ({captures} and {query vars}); the site base is prepended
    page: str                                 # page spec in selectors.yaml
    kind: str = "rows"                        # rows | object
    wrap: str = "raw"                         # envelope the shared normalizers expect (see cli.commandmap.shape)
    adapt: Callable[[Any, RequestSpec], Any] | None = None
    download: bool = False                    # the page lives behind a native download (attachments)
    unsupported_params: tuple[str, ...] = ()  # request parameters the UI page cannot honour (refused, never silently ignored)


@dataclass(frozen=True)
class Write:
    op: str
    path: str
    url: str
    steps: Callable[[RequestSpec, dict[str, str]], list[Step]]
    result_page: str | None = None            # re-read this page after the flow to build the REST response
    result: str = "none"                      # none | last | list | object
    method: str = "POST"


Binding = Read | Write


def captures(template: str, path: str) -> dict[str, str] | None:
    m = re.match("^" + re.sub(r"\\\{(\w+)\\\}", r"(?P<\1>[^/]+)", re.escape(template)) + "$", path)
    return m.groupdict() if m else None


def render_url(template: str, values: Mapping[str, Any]) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: quote(str(values.get(m.group(1), "")), safe=""), template)


def query_vars(spec: RequestSpec) -> dict[str, Any]:
    body = spec.json if isinstance(spec.json, Mapping) else {}
    return {**{k: v for k, v in spec.params.items() if v is not None}, **{k: v for k, v in body.items() if not isinstance(v, (Mapping, list))}}


def only(spec: RequestSpec, allowed: set[str], op: str) -> None:
    extra = [leaf for leaf in _leaves(spec.json) if leaf not in allowed]
    if extra:
        raise UnsupportedOperationError(f"{op}: the browser flow cannot express {', '.join(extra)}", operation=op,
                                        hint="Use the REST or MCP backend for this payload; the router falls back.")


# ---------------------------------------------------------------------------------------------------------------------- read adapters
def public_only(rows: Any, spec: RequestSpec) -> Any:
    return [r for r in rows if r.get("public")] if str(spec.params.get("public")) == "true" else rows


def restrictions(rows: Any, spec: RequestSpec) -> Any:
    return {r["operation"]: {"operation": r["operation"], "restrictions": {"user": {"results": [{"displayName": u, "accountId": u} for u in r.get("users", [])]},
                                                                          "group": {"results": [{"name": g} for g in r.get("groups", [])]}}} for r in rows}


def webhooks(rows: Any, spec: RequestSpec) -> Any:
    return [{**r, "events": r.get("events", [])} for r in rows]


# ---------------------------------------------------------------------------------------------------------------------- write flows
def s_comment(spec: RequestSpec, c: dict[str, str]) -> list[Step]:
    only(spec, {"body"}, "jira.issue.comment.add")
    return [("fill", "[data-testid='comment.input']", str(_dig(spec.json, "body"))), ("click", "[data-testid='comment.submit']")]


def s_transition(spec: RequestSpec, c: dict[str, str]) -> list[Step]:
    only(spec, {"transition.id"}, "jira.issue.transition")
    return [("click", f"[data-transition-id='{_dig(spec.json, 'transition', 'id')}']")]


def s_edit(spec: RequestSpec, c: dict[str, str]) -> list[Step]:
    only(spec, {"fields.summary"}, "jira.issue.edit")
    return [("fill", "[data-testid='summary.input']", str(_dig(spec.json, "fields", "summary"))), ("click", "[data-testid='summary.save']")]


def s_link(spec: RequestSpec, c: dict[str, str]) -> list[Step]:
    only(spec, {"type.name", "inwardIssue.key", "outwardIssue.key"}, "jira.issue.link.create")
    return [("fill", "[data-testid='link.type']", str(_dig(spec.json, "type", "name"))), ("fill", "[data-testid='link.issue']", str(_dig(spec.json, "outwardIssue", "key"))),
            ("click", "[data-testid='link.submit']")]


def s_upload(spec: RequestSpec, c: dict[str, str]) -> list[Step]:
    name, data = next(iter((spec.files or {}).values()))[:2]
    return [("attach", "input[type='file'][data-testid='attach.input']", str(name), bytes(data)), ("click", "[data-testid='attach.submit']")]


def s_sprint(spec: RequestSpec, c: dict[str, str]) -> list[Step]:
    only(spec, {"issues"}, "jira.agile.issue.move_to_sprint")
    return [("fill", "[data-testid='sprint.add.input']", ",".join((spec.json or {}).get("issues", []))), ("click", "[data-testid='sprint.add.submit']")]


def s_jsm_comment(spec: RequestSpec, c: dict[str, str]) -> list[Step]:
    only(spec, {"body", "public"}, "jsm.request.comment.add")
    return [("fill", "[data-testid='jsm.comment.input']", str(_dig(spec.json, "body"))),
            ("fill", "[data-testid='jsm.comment.visibility']", "public" if _dig(spec.json, "public") else "internal"), ("click", "[data-testid='jsm.comment.submit']")]


def s_conf_update(spec: RequestSpec, c: dict[str, str]) -> list[Step]:
    only(spec, {"id", "status", "title", "body.representation", "body.value", "version.number", "version.message"}, "confluence.content.update")
    b = spec.json or {}
    return [("fill", "[data-testid='editor.title']", str(b.get("title", ""))), ("fill", "[data-testid='editor.body']", str(_dig(b, "body", "value"))),
            ("fill", "[data-testid='editor.version']", str(_dig(b, "version", "number"))), ("click", "[data-testid='editor.publish']")]


PAGE = "/wiki/spaces/-/pages/{content_id}"

READS: tuple[Read, ...] = (
    Read("jira.issue.get", "/issue/{key}", "/browse/{key}", "issue.view", "object"),
    Read("jira.issue.search", "/search/jql", "/issues/?jql={jql}", "issue.row", wrap="token:issues"),
    Read("jira.issue.count", "/search/approximate-count", "/issues/?jql={jql}", "issue.total", "object"),
    Read("jira.issue.comments.list", "/issue/{key}/comment", "/browse/{key}", "issue.comments", wrap="offset:comments"),
    Read("jira.issue.changelog.list", "/issue/{key}/changelog", "/browse/{key}", "issue.history", wrap="offset:values"),
    Read("jira.issue.worklogs.list", "/issue/{key}/worklog", "/browse/{key}", "issue.worklogs", wrap="offset:worklogs"),
    Read("jira.issue.transitions.list", "/issue/{key}/transitions", "/browse/{key}", "issue.transitions", wrap="obj:transitions"),
    Read("jira.issue.attachments.list", "/issue/{key}", "/browse/{key}", "issue.attachments", wrap="fields:attachment"),
    Read("jira.issue.attachment.download", "/attachment/{attachment_id}", "/secure/attachment/{attachment_id}/", "", download=True),
    Read("jira.project.list", "/project/search", "/jira/projects", "project.row", wrap="offset:values"),
    Read("jira.project.get", "/project/{key}", "/projects/{key}", "project.view", "object"),
    Read("jira.project.components.list", "/project/{project}/components", "/projects/{project}/components", "project.components"),
    Read("jira.project.versions.list", "/project/{project}/versions", "/projects/{project}/versions", "project.versions"),
    Read("jira.filter.list", "/filter/search", "/jira/filters", "filter.row", wrap="offset:values"),
    Read("jira.dashboard.list", "/dashboard/search", "/jira/dashboards", "dashboard.row", wrap="offset:values"),
    Read("jira.agile.board.list", "/board", "/jira/software/boards", "board.row", wrap="offset:values"),
    Read("jira.agile.board.get", "/board/{board_id}", "/jira/software/boards/{board_id}", "board.view", "object"),
    Read("jira.agile.board.config", "/board/{board_id}/configuration", "/jira/software/boards/{board_id}/settings", "board.config", "object"),
    Read("jira.agile.board.issues", "/board/{board_id}/issue", "/jira/software/boards/{board_id}/issues", "board.issues", wrap="offset:issues"),
    Read("jira.agile.sprint.list", "/board/{board_id}/sprint", "/jira/software/boards/{board_id}/sprints", "sprint.row", wrap="offset:values"),
    Read("jira.agile.sprint.get", "/sprint/{sprint_id}", "/jira/software/sprints/{sprint_id}", "sprint.view", "object"),
    Read("jira.agile.sprint.issues", "/sprint/{sprint_id}/issue", "/jira/software/sprints/{sprint_id}/issues", "sprint.issues", wrap="offset:issues"),
    Read("jira.agile.backlog.list", "/board/{board_id}/backlog", "/jira/software/boards/{board_id}/backlog", "board.backlog", wrap="offset:issues"),
    Read("jira.agile.epic.list", "/board/{board_id}/epic", "/jira/software/boards/{board_id}/epics", "board.epics", wrap="offset:values"),
    Read("jsm.info", "/info", "/servicedesk/about", "jsm.info", "object"),
    Read("jsm.service_desk.list", "/servicedesk", "/servicedesk/desks", "jsm.desk", wrap="jsm:values"),
    Read("jsm.request_type.list", "/servicedesk/{service_desk_id}/requesttype", "/servicedesk/{service_desk_id}/request-types", "jsm.requesttype", wrap="jsm:values"),
    Read("jsm.request.list", "/request", "/servicedesk/requests?desk={serviceDeskId}&status={requestStatus}", "jsm.request.row", wrap="jsm:values"),
    Read("jsm.request.get", "/request/{key}", "/servicedesk/requests/{key}", "jsm.request.view", "object"),
    Read("jsm.request.comments", "/request/{key}/comment", "/servicedesk/requests/{key}", "jsm.comments", wrap="jsm:values", adapt=public_only),
    Read("jsm.request.sla", "/request/{key}/sla", "/servicedesk/requests/{key}", "jsm.sla", wrap="obj:values"),
    Read("jsm.kb.search", "/knowledgebase/article", "/servicedesk/kb?query={query}", "jsm.kb", wrap="obj:values"),
    Read("jsm.organization.list", "/organization", "/servicedesk/organizations", "jsm.org", wrap="jsm:values"),
    Read("jsm.customer.list", "/servicedesk/{service_desk_id}/customer", "/servicedesk/{service_desk_id}/customers", "jsm.customer", wrap="jsm:values"),
    Read("confluence.content.get", "/pages/{content_id}", "/wiki/spaces/-/pages/{content_id}", "conf.page.view", "object", unsupported_params=("version",)),
    Read("confluence.content.list", "/pages", "/wiki/pages?space={space_id}&title={title}", "conf.page.row", wrap="link:results"),
    Read("confluence.space.get", "/spaces/{key_or_id}", "/wiki/spaces/{key_or_id}/overview", "conf.space.view", "object"),
    Read("confluence.space.list", "/spaces", "/wiki/spaces", "conf.space.row", wrap="link:results"),
    Read("confluence.search.cql", "/search", "/wiki/search?cql={cql}", "conf.search", wrap="cql:results"),
    Read("confluence.content.children", "/pages/{content_id}/children", "/wiki/spaces/-/pages/{content_id}/children", "conf.children", wrap="link:results"),
    Read("confluence.content.ancestors", "/pages/{content_id}/ancestors", "/wiki/spaces/-/pages/{content_id}/ancestors", "conf.ancestors", wrap="obj:results"),
    Read("confluence.content.versions", "/pages/{content_id}/versions", "/wiki/pages/{content_id}/history", "conf.versions", wrap="link:results"),
    Read("confluence.comment.list", "/pages/{content_id}/footer-comments", PAGE, "conf.comments.footer", wrap="link:results"),
    Read("confluence.comment.list", "/pages/{content_id}/inline-comments", PAGE, "conf.comments.inline", wrap="link:results"),
    Read("confluence.attachment.list", "/pages/{content_id}/attachments", "/wiki/pages/viewpageattachments.action?pageId={content_id}", "conf.attachments", wrap="link:results"),
    Read("confluence.attachment.download", "/attachments/{attachment_id}", "/wiki/attachment/{attachment_id}", "conf.attachment.meta", "object", download=True),
    Read("confluence.label.list", "/pages/{content_id}/labels", PAGE, "conf.labels", wrap="obj:results"),
    Read("confluence.content.macros.resolve", "/pages/{content_id}", "/wiki/spaces/-/pages/{content_id}?view=rendered", "conf.macros", "object", unsupported_params=("version",)),
    Read("confluence.permission.restrictions.get", "/content/{content_id}/restriction/byOperation", "/wiki/spaces/-/pages/{content_id}/restrictions", "conf.restrictions", adapt=restrictions),
    Read("webhook.jira.admin.list", "/webhook", "/plugins/servlet/webhooks", "webhook.row", adapt=webhooks),
)

WRITES: tuple[Write, ...] = (
    Write("jira.issue.comment.add", "/issue/{key}/comment", "/browse/{key}", s_comment, "issue.comments", "last"),
    Write("jira.issue.transition", "/issue/{key}/transitions", "/browse/{key}", s_transition),
    Write("jira.issue.edit", "/issue/{key}", "/browse/{key}", s_edit, method="PUT"),
    Write("jira.issue.link.create", "/issueLink", "/browse/{key}", s_link),
    Write("jira.issue.attachment.upload", "/issue/{key}/attachments", "/browse/{key}", s_upload, "issue.attachments", "list"),
    Write("jira.agile.issue.move_to_sprint", "/sprint/{sprint_id}/issue", "/jira/software/sprints/{sprint_id}/issues", s_sprint),
    Write("jsm.request.comment.add", "/request/{key}/comment", "/servicedesk/requests/{key}", s_jsm_comment, "jsm.comments", "last"),
    Write("confluence.content.update", "/pages/{content_id}", "/wiki/spaces/-/pages/{content_id}/edit", s_conf_update, "conf.page.view", "object", method="PUT"),
)
BINDINGS: tuple[Binding, ...] = (*READS, *WRITES)
WRITE_OPS = frozenset(w.op for w in WRITES)

__all__ = ["BINDINGS", "READS", "WRITES", "WRITE_OPS", "Read", "Write", "captures", "render_url", "query_vars", "shape"]
