"""Operation -> SQL bindings (design 11, Data Lake column). Each handler runs SQL over the lake tables and returns the REST wire shape the shared normalizers read.
Column and table names come from ``schema.py`` (VER-10)."""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ...core.errors import NotFoundError
from ...core.http import RequestSpec
from ..cli.commandmap import shape
from ..optransport import Handler
from .jql import cql_to_sql, jql_to_sql
from .lake import Lake

Fn = Callable[[Lake, RequestSpec, dict[str, str]], Awaitable[Any]]


def b(x: Any) -> bool:
    return str(x).strip().lower() in ("1", "true", "yes")


def user(r: Mapping[str, Any], prefix: str) -> dict[str, Any] | None:
    ident = r.get(f"{prefix}_id")
    if ident is None and r.get(f"{prefix}_name") is None:
        return None
    return {"accountId": ident, "displayName": r.get(f"{prefix}_name"), "emailAddress": r.get(f"{prefix}_email"), "active": True}


def issue_json(r: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": str(r["issue_id"]), "key": r["issue_key"], "fields": {
        "summary": r["summary"], "description": r.get("description"), "status": {"name": r["status"], "statusCategory": {"name": r.get("status_category")}},
        "issuetype": {"name": r.get("issue_type")}, "project": {"key": r["project_key"]}, "priority": {"name": r.get("priority")},
        "assignee": user(r, "assignee"), "reporter": user(r, "reporter"), "labels": [x for x in (r.get("labels") or "").split(",") if x],
        "created": r.get("created"), "updated": r.get("updated")}}


def page_json(r: Mapping[str, Any], *, body: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {"id": str(r["page_id"]), "status": r.get("status") or "current", "title": r["title"], "spaceId": str(r["space_id"]) if r.get("space_id") else None,
                           "parentId": str(r["parent_id"]) if r.get("parent_id") else None, "authorId": r.get("author_id"), "createdAt": r.get("created"),
                           "version": {"number": int(r["version"]) if r.get("version") is not None else 1, "createdAt": r.get("updated")},
                           "_links": {"webui": r.get("web_url")}}
    if body:
        out["body"] = {"storage": {"value": r.get("body_storage") or ""}}
    return out


def space_json(r: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": str(r["space_id"]), "key": r["space_key"], "name": r.get("name"), "type": r.get("space_type"), "status": r.get("status"),
            "homepageId": str(r["homepage_id"]) if r.get("homepage_id") else None}


# ---------------------------------------------------------------------------------------------------------------------- jira
async def issue_get(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jira_issue"], "SELECT * FROM jira_issue WHERE issue_key = ?", [c["key"].upper()])
    if not rows:
        raise NotFoundError(f"issue {c['key']} is not in the lake", hint="Not found OR the share lags behind Jira. Verify the key, or use REST for fresh data.")
    return issue_json(rows[0])


async def issue_search(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    where, params, order = jql_to_sql((spec.json or {}).get("jql", ""))
    rows = await lake.select(["jira_issue"], f"SELECT * FROM jira_issue WHERE {where}{order or ' ORDER BY issue_key'}", params)
    return shape("token:issues", [issue_json(r) for r in rows], spec)


async def issue_count(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    where, params, _ = jql_to_sql((spec.json or {}).get("jql", ""))
    rows = await lake.select(["jira_issue"], f"SELECT COUNT(*) AS n FROM jira_issue WHERE {where}", params)
    return {"count": int(rows[0]["n"])}


async def comments(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jira_comment"], "SELECT * FROM jira_comment WHERE issue_key = ? ORDER BY created, comment_id", [c["key"].upper()])
    items = [{"id": str(r["comment_id"]), "author": user(r, "author"), "body": r.get("body"), "created": r.get("created"), "updated": r.get("updated")} for r in rows]
    return shape("offset:comments", items, spec)


async def changelog(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jira_changelog"], "SELECT * FROM jira_changelog WHERE issue_key = ? ORDER BY created, history_id", [c["key"].upper()])
    grouped: dict[str, dict[str, Any]] = {}
    for r in rows:
        h = grouped.setdefault(str(r["history_id"]), {"id": str(r["history_id"]), "author": user(r, "author"), "created": r.get("created"), "items": []})
        h["items"].append({"field": r["field"], "fromString": r.get("from_string"), "toString": r.get("to_string")})
    return shape("offset:values", list(grouped.values()), spec)


async def worklogs(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jira_worklog"], "SELECT * FROM jira_worklog WHERE issue_key = ? ORDER BY started", [c["key"].upper()])
    items = [{"id": str(r["worklog_id"]), "author": user(r, "author"), "timeSpent": r.get("time_spent"), "timeSpentSeconds": int(r["time_spent_seconds"]) if r.get("time_spent_seconds") is not None else None,
              "started": r.get("started")} for r in rows]
    return shape("offset:worklogs", items, spec)


def project_json(r: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": str(r["project_id"]), "key": r["project_key"], "name": r["name"], "projectTypeKey": r.get("project_type"),
            "lead": {"accountId": r.get("lead_id"), "displayName": r.get("lead_name")} if r.get("lead_id") or r.get("lead_name") else None}


async def project_list(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    q = spec.params.get("query")
    rows = await lake.select(["jira_project"], "SELECT * FROM jira_project WHERE (? IS NULL OR name LIKE ? OR project_key LIKE ?) ORDER BY project_id", [q, f"%{q}%", f"%{q}%"])
    return shape("offset:values", [project_json(r) for r in rows], spec)


async def project_get(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jira_project"], "SELECT * FROM jira_project WHERE project_key = ?", [c["key"].upper()])
    if not rows:
        raise NotFoundError(f"project {c['key']} is not in the lake", hint="Not found OR the share lags behind Jira.")
    return project_json(rows[0])


async def components(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jira_component"], "SELECT * FROM jira_component WHERE project_key = ?", [c["project"].upper()])
    return [{"id": str(r["component_id"]), "name": r["name"], "lead": {"displayName": r["lead_name"]} if r.get("lead_name") else None} for r in rows]


async def versions(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jira_version"], "SELECT * FROM jira_version WHERE project_key = ?", [c["project"].upper()])
    return [{"id": str(r["version_id"]), "name": r["name"], "released": b(r.get("released")), "releaseDate": r.get("release_date")} for r in rows]


async def fields(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jira_field"], "SELECT * FROM jira_field ORDER BY field_id")
    return [{"id": r["field_id"], "name": r["name"], "custom": b(r.get("is_custom")), "schema": {"type": r.get("schema_type")}} for r in rows]


async def issue_types(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jira_issue_type"], "SELECT * FROM jira_issue_type ORDER BY issue_type_id")
    return [{"id": str(r["issue_type_id"]), "name": r["name"], "subtask": b(r.get("is_subtask"))} for r in rows]


async def statuses(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jira_status"], "SELECT * FROM jira_status ORDER BY status_id")
    return [{"id": str(r["status_id"]), "name": r["name"], "statusCategory": {"name": r.get("category")}} for r in rows]


async def priorities(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    return [{"name": r["name"]} for r in await lake.select(["jira_priority"], "SELECT * FROM jira_priority")]


async def link_types(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    return {"issueLinkTypes": [{"name": r["name"]} for r in await lake.select(["jira_link_type"], "SELECT * FROM jira_link_type")]}


def user_row(r: Mapping[str, Any]) -> dict[str, Any]:
    return {"accountId": r["account_id"], "displayName": r.get("display_name"), "emailAddress": r.get("email"), "active": b(r.get("active", True))}


async def user_get(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jira_user"], "SELECT * FROM jira_user WHERE account_id = ?", [spec.params.get("accountId")])
    if not rows:
        raise NotFoundError("no such user in the lake", hint="Not found OR the user is not in the share.")
    return user_row(rows[0])


async def user_search(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    q = f"%{spec.params.get('query') or ''}%"
    rows = await lake.select(["jira_user"], "SELECT * FROM jira_user WHERE display_name LIKE ? OR email LIKE ? ORDER BY account_id LIMIT ?", [q, q, int(spec.params.get("maxResults", 50))])
    return [user_row(r) for r in rows]


# ---------------------------------------------------------------------------------------------------------------------- agile
async def board_list(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jira_board"], "SELECT * FROM jira_board WHERE (? IS NULL OR project_key = ?) AND (? IS NULL OR name LIKE ?) ORDER BY board_id",
                             [spec.params.get("projectKeyOrId"), spec.params.get("projectKeyOrId"), spec.params.get("name"), f"%{spec.params.get('name')}%"])
    return shape("offset:values", [board_json(r) for r in rows], spec)


def board_json(r: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": int(r["board_id"]), "name": r["name"], "type": r.get("board_type"), "location": {"projectKey": r.get("project_key")}}


async def board_get(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jira_board"], "SELECT * FROM jira_board WHERE board_id = ?", [c["board_id"]])
    if not rows:
        raise NotFoundError(f"board {c['board_id']} is not in the lake", hint="Not found OR the share lags behind Jira.")
    return board_json(rows[0])


def sprint_json(r: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": int(r["sprint_id"]), "name": r["name"], "state": r.get("state"), "goal": r.get("goal"), "startDate": r.get("start_date"), "endDate": r.get("end_date")}


async def sprint_list(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    states = [s for s in str(spec.params.get("state") or "").split(",") if s]
    marks = ", ".join("?" for _ in states)
    rows = await lake.select(["jira_sprint"], f"SELECT * FROM jira_sprint WHERE board_id = ?{f' AND state IN ({marks})' if states else ''} ORDER BY sprint_id", [c["board_id"], *states])
    return shape("offset:values", [sprint_json(r) for r in rows], spec)


async def sprint_get(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jira_sprint"], "SELECT * FROM jira_sprint WHERE sprint_id = ?", [c["sprint_id"]])
    if not rows:
        raise NotFoundError(f"sprint {c['sprint_id']} is not in the lake", hint="Not found OR the share lags behind Jira.")
    return sprint_json(rows[0])


async def sprint_issues(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jira_issue", "jira_sprint_issue"], "SELECT i.* FROM jira_issue i JOIN jira_sprint_issue s ON s.issue_key = i.issue_key WHERE s.sprint_id = ? ORDER BY i.issue_key", [c["sprint_id"]])
    return shape("offset:issues", [issue_json(r) for r in rows], spec)


async def epics(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jira_epic"], "SELECT * FROM jira_epic WHERE board_id = ? ORDER BY epic_id", [c["board_id"]])
    return shape("offset:values", [{"id": int(r["epic_id"]), "key": r["epic_key"], "name": r.get("name"), "done": b(r.get("is_done"))} for r in rows], spec)


# ---------------------------------------------------------------------------------------------------------------------- JSM
async def jsm_info(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jsm_info"], "SELECT * FROM jsm_info LIMIT 1")
    r = rows[0] if rows else {}
    return {"version": r.get("version"), "platformVersion": r.get("platform_version"), "isLicensedForUse": b(r.get("licensed", True))}


async def desks(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jsm_service_desk"], "SELECT * FROM jsm_service_desk ORDER BY service_desk_id")
    return shape("jsm:values", [{"id": str(r["service_desk_id"]), "projectId": str(r["project_id"]), "projectKey": r["project_key"], "projectName": r["project_name"]} for r in rows], spec)


async def request_types(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jsm_request_type"], "SELECT * FROM jsm_request_type WHERE service_desk_id = ? ORDER BY request_type_id", [c["service_desk_id"]])
    return shape("jsm:values", [{"id": str(r["request_type_id"]), "name": r["name"], "description": r.get("description"), "serviceDeskId": str(r["service_desk_id"])} for r in rows], spec)


def request_json(r: Mapping[str, Any]) -> dict[str, Any]:
    return {"issueId": str(r["issue_id"]), "issueKey": r["issue_key"], "requestTypeId": str(r.get("request_type_id") or ""), "serviceDeskId": str(r.get("service_desk_id") or ""),
            "currentStatus": {"status": r.get("status")}, "reporter": user(r, "reporter"), "createdDate": {"iso8601": r.get("created")},
            "requestFieldValues": [{"fieldId": "summary", "value": r.get("summary")}, {"fieldId": "description", "value": r.get("description")}]}


async def requests_list(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    p = spec.params
    rows = await lake.select(["jsm_request"], "SELECT * FROM jsm_request WHERE (? IS NULL OR service_desk_id = ?) AND (? IS NULL OR status = ?) ORDER BY issue_key",
                             [p.get("serviceDeskId"), p.get("serviceDeskId"), p.get("requestStatus"), p.get("requestStatus")])
    return shape("jsm:values", [request_json(r) for r in rows], spec)


async def request_get(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jsm_request"], "SELECT * FROM jsm_request WHERE issue_key = ?", [c["key"].upper()])
    if not rows:
        raise NotFoundError(f"request {c['key']} is not in the lake", hint="Not found OR the share lags behind JSM.")
    return request_json(rows[0])


async def request_comments(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    only_public = str(spec.params.get("public")) == "true"
    rows = await lake.select(["jsm_request_comment"], "SELECT * FROM jsm_request_comment WHERE issue_key = ? ORDER BY created", [c["key"].upper()])
    items = [{"id": str(r["comment_id"]), "author": user(r, "author"), "body": r.get("body"), "public": b(r.get("is_public")), "created": {"iso8601": r.get("created")}} for r in rows]
    return shape("jsm:values", [i for i in items if i["public"] or not only_public], spec)


async def request_sla(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jsm_request_sla"], "SELECT * FROM jsm_request_sla WHERE issue_key = ?", [c["key"].upper()])
    return {"values": [{"id": str(r["sla_id"]), "name": r["name"], "ongoingCycle": {"breached": b(r.get("breached")), "paused": b(r.get("paused")),
                                                                                 "remainingTime": {"millis": int(r["remaining_millis"]) if r.get("remaining_millis") is not None else None},
                                                                                 "goalDuration": {"millis": int(r["goal_millis"]) if r.get("goal_millis") is not None else None}}} for r in rows]}


async def kb_search(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    q = f"%{spec.params.get('query') or ''}%"
    rows = await lake.select(["jsm_kb_article"], "SELECT * FROM jsm_kb_article WHERE title LIKE ? OR excerpt LIKE ?", [q, q])
    return {"values": [{"title": r["title"], "excerpt": r.get("excerpt"), "content": {"iframeSrc": r.get("url")}} for r in rows]}


async def organizations(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jsm_organization"], "SELECT * FROM jsm_organization ORDER BY organization_id")
    return shape("jsm:values", [{"id": str(r["organization_id"]), "name": r.get("name")} for r in rows], spec)


async def customers(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["jsm_customer"], "SELECT * FROM jsm_customer WHERE service_desk_id = ? ORDER BY account_id", [c["service_desk_id"]])
    return shape("jsm:values", [{"accountId": r["account_id"], "displayName": r.get("display_name"), "emailAddress": r.get("email"), "active": True} for r in rows], spec)


# ---------------------------------------------------------------------------------------------------------------------- confluence
async def page_get(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["confluence_page"], "SELECT * FROM confluence_page WHERE page_id = ?", [c["content_id"]])
    if not rows:
        raise NotFoundError(f"page {c['content_id']} is not in the lake", hint="Not found OR the share lags behind Confluence.")
    return page_json(rows[0], body="body-format" in spec.params)         # no body-format parameter means "none"


async def page_list(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["confluence_page"], "SELECT * FROM confluence_page WHERE (? IS NULL OR title = ?) AND status = 'current' ORDER BY page_id",
                             [spec.params.get("title"), spec.params.get("title")])
    return shape("link:results", [page_json(r, body=False) for r in rows], spec)


async def space_get(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    rows = await lake.select(["confluence_space"], "SELECT * FROM confluence_space WHERE space_key = ? OR space_id = ?", [c["key_or_id"], c["key_or_id"]])
    if not rows:
        raise NotFoundError(f"space {c['key_or_id']} is not in the lake", hint="Not found OR the share lags behind Confluence.")
    return space_json(rows[0])


async def space_list(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    keys = [k for k in str(spec.params.get("keys") or "").split(",") if k]
    marks = ", ".join("?" for _ in keys)
    rows = await lake.select(["confluence_space"], f"SELECT * FROM confluence_space{f' WHERE space_key IN ({marks})' if keys else ''} ORDER BY space_id", keys)
    return shape("link:results", [space_json(r) for r in rows], spec)


async def cql(lake: Lake, spec: RequestSpec, c: dict[str, str]) -> Any:
    where, params = cql_to_sql(str(spec.params.get("cql", "")))
    rows = await lake.select(["confluence_page"], f"SELECT * FROM confluence_page WHERE {where} AND status = 'current' ORDER BY page_id", params)
    items = [{"content": {"id": str(r["page_id"]), "type": "page", "title": r["title"]}, "title": r["title"], "excerpt": re.sub(r"<[^>]+>", " ", r.get("body_storage") or "").strip()[:200],
              "url": r.get("web_url"), "lastModified": r.get("updated")} for r in rows]
    return shape("cql:results", items, spec)


@dataclass(frozen=True)
class Binding:
    op: str
    path: str
    fn: Fn


def _b(op: str, path: str, fn: Fn) -> Binding:
    return Binding(op, path, fn)


BINDINGS: tuple[Binding, ...] = (
    _b("jira.issue.get", "/issue/{key}", issue_get), _b("jira.issue.search", "/search/jql", issue_search), _b("jira.issue.count", "/search/approximate-count", issue_count),
    _b("jira.issue.comments.list", "/issue/{key}/comment", comments), _b("jira.issue.changelog.list", "/issue/{key}/changelog", changelog),
    _b("jira.issue.worklogs.list", "/issue/{key}/worklog", worklogs),
    _b("jira.project.list", "/project/search", project_list), _b("jira.project.get", "/project/{key}", project_get),
    _b("jira.project.components.list", "/project/{project}/components", components), _b("jira.project.versions.list", "/project/{project}/versions", versions),
    _b("jira.meta.fields.list", "/field", fields), _b("jira.meta.issue_types.list", "/issuetype", issue_types), _b("jira.meta.statuses.list", "/status", statuses),
    _b("jira.meta.priorities.list", "/priority", priorities), _b("jira.meta.link_types.list", "/issueLinkType", link_types),
    _b("jira.user.get", "/user", user_get), _b("jira.user.search", "/user/search", user_search),
    _b("jira.agile.board.list", "/board", board_list), _b("jira.agile.board.get", "/board/{board_id}", board_get), _b("jira.agile.sprint.list", "/board/{board_id}/sprint", sprint_list),
    _b("jira.agile.sprint.get", "/sprint/{sprint_id}", sprint_get), _b("jira.agile.sprint.issues", "/sprint/{sprint_id}/issue", sprint_issues),
    _b("jira.agile.epic.list", "/board/{board_id}/epic", epics),
    _b("jsm.info", "/info", jsm_info), _b("jsm.service_desk.list", "/servicedesk", desks), _b("jsm.request_type.list", "/servicedesk/{service_desk_id}/requesttype", request_types),
    _b("jsm.request.list", "/request", requests_list), _b("jsm.request.get", "/request/{key}", request_get), _b("jsm.request.comments", "/request/{key}/comment", request_comments),
    _b("jsm.request.sla", "/request/{key}/sla", request_sla), _b("jsm.kb.search", "/knowledgebase/article", kb_search), _b("jsm.organization.list", "/organization", organizations),
    _b("jsm.customer.list", "/servicedesk/{service_desk_id}/customer", customers),
    _b("confluence.content.get", "/pages/{content_id}", page_get), _b("confluence.content.list", "/pages", page_list), _b("confluence.space.get", "/spaces/{key_or_id}", space_get),
    _b("confluence.space.list", "/spaces", space_list), _b("confluence.search.cql", "/search", cql),
)


def handlers(lake: Lake, ops: frozenset[str] | None = None) -> dict[str, Handler]:
    out: dict[str, Handler] = {}
    for bnd in BINDINGS:
        if ops is not None and bnd.op not in ops:
            continue

        def make(bd: Binding) -> Handler:
            async def handle(spec: RequestSpec) -> Any:
                m = re.match("^" + re.sub(r"\\\{(\w+)\\\}", r"(?P<\1>[^/]+)", re.escape(bd.path)) + "$", spec.path)
                if m is None:
                    from ...core.errors import UnsupportedOperationError

                    raise UnsupportedOperationError(f"{bd.op}: no lake query for {spec.path}", operation=bd.op, backend="data_lake")
                return await bd.fn(lake, spec, m.groupdict())
            return handle
        out[bnd.op] = make(bnd)
    return out
