"""Bindings from canonical operations to stored GraphQL documents (design 10.4), checked against the live Atlassian GraphQL gateway (DCR-007).

A binding says which document answers which REST-shaped request, how to derive the GraphQL variables from it, and how to turn the GraphQL result back into
the REST wire shape the shared normalizers read. This module is the ONLY place that knows the gateway's field names; the documents live in ``queries/``.

Live facts (2026-10-08): Jira issues expose typed top-level fields (``summary``, ``status``, ``assignee`` ...) and ADF under ``description.adfValue.json``;
search is ``issueSearchStable`` behind ``X-ExperimentalApi: JiraIssueSearch`` and pages with real cursors; Confluence objects are addressed by ARI
(``ari:cloud:confluence:<cloudId>:page/<id>``) and ``confluence`` takes no ``cloudId`` argument at run time even though the schema lists one.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ...core.errors import NotFoundError, UnsupportedOperationError
from ...core.http import RequestSpec
from ..cli.commandmap import shape

Vars = Callable[[RequestSpec, dict[str, str], str], dict[str, Any]]          # (spec, path captures, cloudId) -> variables
Adapt = Callable[[dict[str, Any], RequestSpec], Any]
FIRST = 100                                                                    # page size asked from the gateway; REST-style windows are applied client-side


@dataclass(frozen=True)
class Binding:
    op: str
    path: str                                                      # REST path template this binding answers ("" = any)
    document: str
    variables: Vars
    adapt: Adapt

    def captures(self, spec: RequestSpec) -> dict[str, str] | None:
        if not self.path:
            return {}
        m = re.match("^" + re.sub(r"\\\{(\w+)\\\}", r"(?P<\1>[^/]+)", re.escape(self.path)) + "$", spec.path)
        return m.groupdict() if m else None


def conn(x: Any) -> list[Any]:
    return [e["node"] for e in ((x or {}).get("edges") or []) if e and e.get("node") is not None]


def body_of(spec: RequestSpec) -> Mapping[str, Any]:
    return spec.json if isinstance(spec.json, Mapping) else {}


def ari(cloud_id: str, kind: str, ident: str) -> str:
    return ident if ident.startswith("ari:") else f"ari:cloud:confluence:{cloud_id}:{kind}/{ident}"


def need(value: Any, what: str) -> Any:
    if value is None:
        raise NotFoundError(f"{what} not found", hint="Not found OR you lack permission to see it.", status=404)
    return value


# ---------------------------------------------------------------------------------------------------------------------- AGG -> REST wire shapes
def user(u: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not u:
        return None
    return {"accountId": u.get("accountId"), "displayName": u.get("name"), **({"emailAddress": u["email"]} if u.get("email") else {})}


def adf(rich: Mapping[str, Any] | None) -> Any:
    return ((rich or {}).get("adfValue") or {}).get("json")


def issue(n: Mapping[str, Any]) -> dict[str, Any]:
    st, pr, it, pj = n.get("status") or {}, n.get("priority"), n.get("issueType") or {}, n.get("project") or {}
    cat = st.get("statusCategory") or {}
    fields = {
        "summary": n.get("summary"), "created": n.get("created"), "updated": n.get("updated"), "resolutiondate": n.get("resolutionDate"), "duedate": n.get("dueDate"),
        "status": {"id": st.get("statusId"), "name": st.get("name"), "statusCategory": {"id": cat.get("statusCategoryId"), "key": cat.get("key"), "name": cat.get("name")}},
        "priority": {"id": pr.get("priorityId"), "name": pr.get("name")} if pr else None,
        "issuetype": {"id": it.get("issueTypeId"), "name": it.get("name")},
        "project": {"id": pj.get("projectId"), "key": pj.get("key"), "name": pj.get("name")},
        "assignee": user(n.get("assignee")), "reporter": user(n.get("reporter")), "resolution": n.get("resolution"),
        "description": adf(n.get("description")), "labels": [lb["name"] for lb in conn(n.get("labels"))],
    }
    return {"id": n.get("issueId"), "key": n.get("key"), "fields": fields}


def comment(c: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": c.get("commentId"), "author": user(c.get("author")), "body": adf(c.get("richText")), "created": c.get("created"), "updated": c.get("updated")}


def worklog(w: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": w.get("worklogId"), "author": user(w.get("author")), "timeSpentSeconds": (w.get("timeSpent") or {}).get("timeInSeconds"),
            "started": w.get("startDate"), "created": w.get("created"), "updated": w.get("updated"), "comment": adf(w.get("workDescription"))}


def project(p: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": p.get("projectId"), "key": p.get("key"), "name": p.get("name"), "projectTypeKey": str(p.get("projectType") or "").lower() or None}


def page(p: Mapping[str, Any]) -> dict[str, Any]:
    ver, links = p.get("latestVersion") or {}, p.get("links") or {}
    out = {"id": p.get("pageId"), "title": p.get("title"), "status": str(p.get("status") or "").lower() or None, "spaceId": (p.get("space") or {}).get("spaceId"),
           "authorId": ((p.get("author") or {}).get("user") or {}).get("accountId"), "createdAt": p.get("createdAt"),
           "version": {"number": ver.get("number"), "createdAt": ver.get("createdAt")} if ver else {},
           "_links": {"webui": links.get("webUi"), "base": links.get("base")}}
    storage = (((p.get("body") or {}).get("storage")) or {}).get("value")
    if storage is not None:
        out["body"] = {"storage": {"value": storage}}
    return out


def conf_comment(c: Mapping[str, Any]) -> dict[str, Any]:
    ver = c.get("latestVersion") or {}
    return {"id": c.get("commentId"), "status": str(c.get("status") or "").lower() or None, "body": {"storage": {"value": (((c.get("body") or {}).get("storage")) or {}).get("value")}},
            "version": {"number": ver.get("number"), "createdAt": ver.get("createdAt"), "authorId": ((c.get("author") or {}).get("user") or {}).get("accountId")}}


# ---------------------------------------------------------------------------------------------------------------------- variables
def v_key(spec: RequestSpec, c: dict[str, str], cloud: str) -> dict[str, Any]:
    return {"cloudId": cloud, "key": c["key"]}


def v_key_first(spec: RequestSpec, c: dict[str, str], cloud: str) -> dict[str, Any]:
    return {"cloudId": cloud, "key": c["key"], "first": FIRST}


def v_search(spec: RequestSpec, c: dict[str, str], cloud: str) -> dict[str, Any]:
    b = body_of(spec)
    return {"cloudId": cloud, "jql": b.get("jql"), "first": min(int(b.get("maxResults") or 50), FIRST), "after": b.get("nextPageToken") or None}


def v_count(spec: RequestSpec, c: dict[str, str], cloud: str) -> dict[str, Any]:
    return {"cloudId": cloud, "jql": body_of(spec).get("jql")}


def v_projects(spec: RequestSpec, c: dict[str, str], cloud: str) -> dict[str, Any]:
    return {"cloudId": cloud, "first": FIRST, "keyword": spec.params.get("query") or None}


def v_none(spec: RequestSpec, c: dict[str, str], cloud: str) -> dict[str, Any]:
    return {}


def v_page(spec: RequestSpec, c: dict[str, str], cloud: str) -> dict[str, Any]:
    if spec.params.get("version") or spec.params.get("body-format") not in (None, "storage"):
        raise UnsupportedOperationError("confluence.content.get: the GraphQL page query returns the current storage body only", operation="confluence.content.get",
                                        hint="REST serves other body formats and historical versions; the router falls back.")
    return {"id": ari(cloud, "page", c["content_id"])}


def v_page_id(spec: RequestSpec, c: dict[str, str], cloud: str) -> dict[str, Any]:
    return {"id": ari(cloud, "page", c["content_id"])}


def v_comments(kind: str) -> Vars:
    return lambda spec, c, cloud: {"id": ari(cloud, "page", c["content_id"]), "commentType": kind}


def v_cql(spec: RequestSpec, c: dict[str, str], cloud: str) -> dict[str, Any]:
    return {"cloudId": cloud, "cql": spec.params.get("cql", ""), "first": FIRST}


# ---------------------------------------------------------------------------------------------------------------------- adapters
def a_issue(d: dict[str, Any], spec: RequestSpec) -> Any:
    return issue(need((d.get("jira") or {}).get("issueByKey"), "issue"))


def a_search(d: dict[str, Any], spec: RequestSpec) -> Any:
    res = (d.get("jira") or {}).get("issueSearchStable") or {}
    info = res.get("pageInfo") or {}
    nxt = info.get("endCursor") if info.get("hasNextPage") else None
    return {"issues": [issue(n) for n in conn(res)], "nextPageToken": nxt, "isLast": nxt is None}


def a_count(d: dict[str, Any], spec: RequestSpec) -> Any:
    return {"count": int(((d.get("jira") or {}).get("issueSearchStable") or {}).get("totalCount") or 0)}


def a_nested(field: str, convert: Callable[[Mapping[str, Any]], dict[str, Any]], wrap: str) -> Adapt:
    def adapt(d: dict[str, Any], spec: RequestSpec) -> Any:
        node = need((d.get("jira") or {}).get("issueByKey"), "issue")
        return shape(wrap, [convert(x) for x in conn(node.get(field))], spec)
    return adapt


def a_projects(d: dict[str, Any], spec: RequestSpec) -> Any:
    return shape("offset:values", [project(p) for p in conn((d.get("jira") or {}).get("allJiraProjects"))], spec)


def a_project(d: dict[str, Any], spec: RequestSpec) -> Any:
    return project(need((d.get("jira") or {}).get("jiraProjectByKey"), "project"))


def a_myself(d: dict[str, Any], spec: RequestSpec) -> Any:
    return need(user((d.get("me") or {}).get("user")), "current user")


def _page(d: dict[str, Any]) -> dict[str, Any]:
    return need((d.get("confluence") or {}).get("page"), "page")          # type: ignore[no-any-return]


def a_page(d: dict[str, Any], spec: RequestSpec) -> Any:
    return page(_page(d))


def a_ancestors(d: dict[str, Any], spec: RequestSpec) -> Any:
    return {"results": [{"id": a.get("pageId"), "type": "page", **{k: v for k, v in page(a).items() if k != "id"}} for a in _page(d).get("ancestors") or []]}


def a_comments(d: dict[str, Any], spec: RequestSpec) -> Any:
    return shape("link:results", [conf_comment(c) for c in _page(d).get("comments") or []], spec)


def a_labels(d: dict[str, Any], spec: RequestSpec) -> Any:
    return {"results": [{"id": lb.get("id"), "name": lb.get("label"), "prefix": lb.get("prefix")} for lb in _page(d).get("labels") or []]}


def a_cql(d: dict[str, Any], spec: RequestSpec) -> Any:
    return shape("cql:results", list(((d.get("confluence_search") or {}).get("nodes")) or []), spec)


def b(op: str, path: str, document: str, variables: Vars, adapt: Adapt) -> Binding:
    return Binding(op, path, document, variables, adapt)


BINDINGS: tuple[Binding, ...] = (
    b("jira.issue.get", "/issue/{key}", "JiraIssueGet", v_key, a_issue),
    b("jira.issue.search", "/search/jql", "JiraIssueSearch", v_search, a_search),
    b("jira.issue.count", "/search/approximate-count", "JiraIssueCount", v_count, a_count),
    b("jira.issue.comments.list", "/issue/{key}/comment", "JiraIssueComments", v_key_first, a_nested("comments", comment, "offset:comments")),
    b("jira.issue.worklogs.list", "/issue/{key}/worklog", "JiraIssueWorklogs", v_key_first, a_nested("worklogs", worklog, "offset:worklogs")),
    b("jira.project.list", "/project/search", "JiraProjects", v_projects, a_projects),
    b("jira.project.get", "/project/{key}", "JiraProject", v_key, a_project),
    b("jira.user.myself", "/myself", "JiraMyself", v_none, a_myself),
    b("confluence.content.get", "/pages/{content_id}", "ConfPage", v_page, a_page),
    b("confluence.content.ancestors", "/pages/{content_id}/ancestors", "ConfPageAncestors", v_page_id, a_ancestors),
    b("confluence.comment.list", "/pages/{content_id}/footer-comments", "ConfPageComments", v_comments("FOOTER"), a_comments),
    b("confluence.comment.list", "/pages/{content_id}/inline-comments", "ConfPageComments", v_comments("INLINE"), a_comments),
    b("confluence.label.list", "/pages/{content_id}/labels", "ConfPageLabels", v_page_id, a_labels),
    b("confluence.search.cql", "/search", "ConfSearch", v_cql, a_cql),
)

__all__ = ["BINDINGS", "Binding"]
