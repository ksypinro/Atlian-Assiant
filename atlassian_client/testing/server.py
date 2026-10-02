"""ScenarioServer: an in-process fake of the Atlassian REST APIs for httpx.MockTransport (design section 18).

It serves the shared scenario dataset in the exact wire shapes and pagination styles of the real APIs (Jira startAt /
nextPageToken, JSM start/isLastPage, Confluence v2 link cursors, Confluence v1 CQL start/limit), so the REST backend can
be exercised end-to-end without a network.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx

from .scenario import scenario

Handler = Callable[[re.Match[str], dict[str, str], Any], tuple[int, Any]]


def _offset_page(items: list[Any], q: dict[str, str], key: str) -> dict[str, Any]:
    start, size = int(q.get("startAt", 0)), int(q.get("maxResults", 50))
    chunk = items[start:start + size]
    return {"startAt": start, "maxResults": size, "total": len(items), "isLast": start + size >= len(items), key: chunk}


def _jsm_page(items: list[Any], q: dict[str, str]) -> dict[str, Any]:
    start, size = int(q.get("start", 0)), int(q.get("limit", 50))
    chunk = items[start:start + size]
    return {"start": start, "limit": size, "size": len(chunk), "isLastPage": start + size >= len(items), "values": chunk}


def _link_page(items: list[Any], q: dict[str, str], path: str) -> dict[str, Any]:
    start, size = int(q.get("cursor", 0)), int(q.get("limit", 25))
    chunk = items[start:start + size]
    body: dict[str, Any] = {"results": chunk, "_links": {}}
    if start + size < len(items):
        body["_links"]["next"] = f"/wiki/api/v2{path}?cursor={start + size}&limit={size}"
    return body


class ScenarioServer:
    def __init__(self, raw: dict[str, Any] | None = None) -> None:
        self.raw = raw or scenario()
        self.requests: list[dict[str, Any]] = []
        self.fail_next: list[tuple[int, dict[str, str]]] = []   # (status, headers) to return before serving normally
        self.routes: list[tuple[str, re.Pattern[str], Handler]] = []
        self._register()

    # ---- plumbing ----
    def route(self, method: str, pattern: str) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            self.routes.append((method, re.compile("^" + pattern + "$"), fn))
            return fn
        return deco

    def __call__(self, request: httpx.Request) -> httpx.Response:
        parts = urlsplit(str(request.url))
        q = dict(parse_qsl(parts.query))
        body = json.loads(request.content) if request.content and request.headers.get("content-type", "").startswith("application/json") else None
        self.requests.append({"method": request.method, "path": parts.path, "query": q, "json": body, "headers": dict(request.headers)})
        if self.fail_next:
            status, headers = self.fail_next.pop(0)
            return httpx.Response(status, headers=headers, json={"errorMessages": [f"forced {status}"]})
        routed = re.sub(r"^/ex/(?:jira|confluence)/[^/]+", "", parts.path)         # OAuth / service-account gateway prefix
        for method, pat, fn in self.routes:
            m = pat.match(routed)
            if request.method == method and m:
                status, payload = fn(m, q, body)
                if isinstance(payload, bytes):
                    return httpx.Response(status, content=payload)
                return httpx.Response(status, json=payload)
        return httpx.Response(404, json={"errorMessages": [f"no route {request.method} {parts.path}"]})

    def client(self, **kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self), **kwargs)

    # ---- routes ----
    def _register(self) -> None:
        r, j, a, s, c = self.route, self.raw["jira"], self.raw["agile"], self.raw["jsm"], self.raw["confluence"]
        issues = {i["key"]: i for i in j["issues"]}
        J, A, S, V2, V1 = r"/rest/api/3", r"/rest/agile/1.0", r"/rest/servicedeskapi", r"/wiki/api/v2", r"/wiki/rest/api"

        def issue_or_404(key: str) -> dict[str, Any] | None:
            return issues.get(key.upper())

        def missing(what: str) -> tuple[int, Any]:
            return 404, {"errorMessages": [f"{what} does not exist or you do not have permission to see it."], "errors": {}}

        @r("GET", "/_edge/tenant_info")
        def tenant(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"cloudId": "cloud-1"}

        @r("GET", J + "/myself")
        def myself(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, j["myself"]

        @r("GET", J + "/user")
        def user(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            u = next((x for x in j["users"] if x["accountId"] == q.get("accountId")), None)
            return (200, u) if u else missing("user")

        @r("GET", J + "/user/search")
        def user_search(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            t = q.get("query", "").lower()
            return 200, [u for u in j["users"] if t in u["displayName"].lower() or t in (u.get("emailAddress") or "").lower()]

        @r("GET", J + "/user/assignable/search")
        def assignable(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, j["users"]

        @r("GET", J + r"/issue/([A-Za-z0-9-]+)")
        def issue_get(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            i = issue_or_404(m.group(1))
            if not i:
                return missing("Issue")
            out = json.loads(json.dumps(i))
            if "names" in q.get("expand", ""):
                out["names"] = {f["id"]: f["name"] for f in j["fields"]}
            if q.get("fields") and q["fields"] not in ("*all", "*navigable"):
                wanted = set(q["fields"].split(","))
                out["fields"] = {k: v for k, v in out["fields"].items() if k in wanted}
            return 200, out

        def sub(path: str, data_key: str, wrap: str, offset: bool = True) -> None:
            @r("GET", J + r"/issue/([A-Za-z0-9-]+)/" + path)
            def _h(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
                if not issue_or_404(m.group(1)):
                    return missing("Issue")
                items = j[data_key].get(m.group(1).upper(), [])
                return 200, _offset_page(items, q, wrap)

        sub("comment", "comments", "comments")
        sub("changelog", "changelog", "values")
        sub("worklog", "worklogs", "worklogs")

        @r("GET", J + r"/issue/([A-Za-z0-9-]+)/transitions")
        def transitions(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return (200, {"transitions": j["transitions"].get(m.group(1).upper(), [])}) if issue_or_404(m.group(1)) else missing("Issue")

        @r("GET", J + r"/issue/([A-Za-z0-9-]+)/remotelink")
        def remotelink(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return (200, j["remote_links"].get(m.group(1).upper(), [])) if issue_or_404(m.group(1)) else missing("Issue")

        @r("GET", J + r"/issue/([A-Za-z0-9-]+)/watchers")
        def watchers(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return (200, {"watchers": j["watchers"].get(m.group(1).upper(), [])}) if issue_or_404(m.group(1)) else missing("Issue")

        @r("GET", J + r"/issue/([A-Za-z0-9-]+)/properties/([^/]+)")
        def prop(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            p = j["properties"].get(m.group(1).upper(), {}).get(m.group(2))
            return (200, p) if p else missing("Property")

        @r("GET", J + r"/attachment/content/([^/]+)")
        def att_content(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            data = j["attachment_content"].get(m.group(1))
            return (200, data) if data is not None else missing("Attachment")

        @r("GET", J + r"/attachment/([^/]+)")
        def att_meta(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            for i in j["issues"]:
                for x in i["fields"].get("attachment", []):
                    if x["id"] == m.group(1):
                        return 200, x
            return missing("Attachment")

        @r("POST", J + "/search/jql")
        def search(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            if not b or not b.get("jql", "").strip():
                return 400, {"errorMessages": ["The JQL query is empty."], "errors": {}}
            start = int(b["nextPageToken"]) if b.get("nextPageToken") else 0
            size = int(b.get("maxResults", 50))
            chunk = j["issues"][start:start + size]
            body: dict[str, Any] = {"issues": chunk}
            if start + size < len(j["issues"]):
                body["nextPageToken"] = str(start + size)
            else:
                body["isLast"] = True
            return 200, body

        @r("POST", J + "/search/approximate-count")
        def count(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"count": len(j["issues"])}

        @r("GET", J + "/project/search")
        def projects(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            t = q.get("query", "").lower()
            return 200, _offset_page([p for p in j["projects"] if t in p["name"].lower() or t in p["key"].lower()], q, "values")

        @r("GET", J + r"/project/(PROJ|OPS)")
        def project(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            p = next(x for x in j["projects"] if x["key"] == m.group(1))
            return 200, {**p, "issueTypes": j["issue_types"]}

        @r("GET", J + r"/project/([A-Z]+)/components")
        def components(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, j["components"].get(m.group(1), [])

        @r("GET", J + r"/project/([A-Z]+)/versions")
        def versions(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, j["versions"].get(m.group(1), [])

        @r("GET", J + r"/project/([A-Z]+)/statuses")
        def pstatuses(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, j["project_statuses"].get(m.group(1), [])

        for path, key in (("/field", "fields"), ("/issuetype", "issue_types"), ("/status", "statuses"), ("/priority", "priorities")):
            self.routes.append(("GET", re.compile("^" + J + path + "$"), (lambda k: lambda m, q, b: (200, j[k]))(key)))

        @r("GET", J + "/issueLinkType")
        def link_types(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"issueLinkTypes": j["link_types"]}

        @r("GET", J + "/serverInfo")
        def server_info(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, j["server_info"]

        @r("GET", J + r"/issue/createmeta/([A-Z]+)/issuetypes/(\d+)")
        def createmeta(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"fields": j["create_fields"].get(f"{m.group(1)}/{m.group(2)}", [])}

        @r("GET", J + "/filter/search")
        def filters(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            t = q.get("filterName", "").lower()
            return 200, _offset_page([f for f in j["filters"] if t in f["name"].lower()], q, "values")

        @r("GET", J + "/dashboard/search")
        def dashboards(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, _offset_page(j["dashboards"], q, "values")

        @r("POST", J + "/jql/parse")
        def jql_parse(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"queries": [{"query": x, "errors": ["Error in the JQL Query"] if "BROKEN" in x else [], "structure": {}} for x in b["queries"]]}

        @r("POST", J + "/jql/sanitize")
        def jql_sanitize(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"queries": [{"initialQuery": x["query"], "sanitizedQuery": x["query"].replace("ann", "acc-ann")} for x in b["queries"]]}

        @r("GET", J + "/jql/autocompletedata")
        def jql_auto(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"visibleFieldNames": [{"value": "status"}]}

        @r("GET", J + "/jql/autocompletedata/suggestions")
        def jql_suggest(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"results": [{"value": "Done", "displayName": "Done"}]}

        @r("POST", J + "/jql/match")
        def jql_match(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"matches": [{"matchedIssues": b["issueIds"], "errors": []} for _ in b["jqls"]]}

        @r("POST", J + "/expression/eval")
        def expr(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"value": 2 if b["expression"].strip() == "1 + 1" else None}

        # ---- agile ----
        @r("GET", A + "/board")
        def boards(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, _offset_page(a["boards"], q, "values")

        @r("GET", A + r"/board/(\d+)")
        def board(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            x = next((x for x in a["boards"] if str(x["id"]) == m.group(1)), None)
            return (200, x) if x else missing("Board")

        @r("GET", A + r"/board/(\d+)/configuration")
        def bconfig(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return (200, a["configs"][m.group(1)]) if m.group(1) in a["configs"] else missing("Board")

        for suffix, key, wrap in (("issue", "board_issues", "issues"), ("backlog", "backlog", "issues"), ("epic", "epics", "values")):
            self.routes.append(("GET", re.compile("^" + A + r"/board/(\d+)/" + suffix + "$"),
                                (lambda k, w: lambda m, q, b: (200, _offset_page(a[k][m.group(1)], q, w)) if m.group(1) in a[k] else (404, {"errorMessages": ["Board does not exist"]}))(key, wrap)))

        @r("GET", A + r"/board/(\d+)/sprint")
        def sprints(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            states = set(q.get("state", "active,future").split(","))
            return 200, _offset_page([x for x in a["sprints"].get(m.group(1), []) if x["state"] in states], q, "values")

        @r("GET", A + r"/sprint/(\d+)")
        def sprint(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            x = next((x for v in a["sprints"].values() for x in v if str(x["id"]) == m.group(1)), None)
            return (200, x) if x else missing("Sprint")

        @r("GET", A + r"/sprint/(\d+)/issue")
        def sprint_issues(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return (200, _offset_page(a["sprint_issues"][m.group(1)], q, "issues")) if m.group(1) in a["sprint_issues"] else missing("Sprint")

        # ---- JSM ----
        @r("GET", S + "/info")
        def jsm_info(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, s["info"]

        @r("GET", S + "/servicedesk")
        def sds(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, _jsm_page(s["service_desks"], q)

        @r("GET", S + r"/servicedesk/(\d+)/requesttype")
        def rts(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return (200, _jsm_page(s["request_types"][m.group(1)], q)) if m.group(1) in s["request_types"] else missing("Service desk")

        @r("GET", S + r"/servicedesk/(\d+)/customer")
        def customers(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return (200, _jsm_page(s["customers"][m.group(1)], q)) if m.group(1) in s["customers"] else missing("Service desk")

        @r("GET", S + "/request")
        def reqs(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            t = q.get("searchTerm", "").lower()
            items = [x for x in s["requests"] if not t or t in json.dumps(x).lower()]
            return 200, _jsm_page(items, q)

        @r("GET", S + r"/request/([A-Z]+-\d+)")
        def req(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            x = next((x for x in s["requests"] if x["issueKey"] == m.group(1)), None)
            return (200, x) if x else missing("Request")

        @r("GET", S + r"/request/([A-Z]+-\d+)/comment")
        def rcomments(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            if m.group(1) not in s["comments"]:
                return missing("Request")
            items = [x for x in s["comments"][m.group(1)] if x["public"] or q.get("public") != "true"]
            return 200, _jsm_page(items, q)

        @r("GET", S + r"/request/([A-Z]+-\d+)/sla")
        def sla(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return (200, {"values": s["sla"][m.group(1)]}) if m.group(1) in s["sla"] else missing("Request")

        @r("GET", S + "/knowledgebase/article")
        def kb(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            t = q.get("query", "").lower()
            return 200, {"values": [x for x in s["kb"] if t in json.dumps(x).lower()]}

        @r("GET", S + "/organization")
        def orgs(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, _jsm_page(s["organizations"], q)

        # ---- Confluence v2 ----
        pages = c["pages"]

        def page_or_404(pid: str) -> dict[str, Any] | None:
            found: dict[str, Any] | None = pages.get(pid)
            return found

        @r("GET", V2 + r"/pages/(\d+)")
        def cpage(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            p = page_or_404(m.group(1))
            if not p:
                return missing("Page")
            out = json.loads(json.dumps(p))
            if q.get("version") and int(q["version"]) != p["version"]["number"]:
                html = c["page_versions"].get(m.group(1), {}).get(q["version"])
                if html is None:
                    return missing("Version")
                out["body"] = {"storage": {"value": html}}
                out["version"]["number"] = int(q["version"])
            fmt = q.get("body-format")
            if fmt == "view":
                out["body"] = {"view": {"value": out["body"]["storage"]["value"]}}
            elif not fmt:
                out.pop("body", None)
            return 200, out

        @r("GET", V2 + r"/blogposts/(\d+)")
        def cblog(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return missing("Blog post")

        @r("GET", V2 + "/pages")
        def cpages(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            items = [p for p in pages.values() if not q.get("title") or p["title"] == q["title"]]
            return 200, _link_page(items, q, "/pages")

        @r("GET", V2 + r"/spaces/(\d+)/pages")
        def space_pages(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            items = [p for p in pages.values() if p["spaceId"] == m.group(1)]
            return 200, _link_page(items, q, f"/spaces/{m.group(1)}/pages")

        def tree(suffix: str, key: str) -> None:
            @r("GET", V2 + r"/pages/(\d+)/" + suffix)
            def _t(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
                if not page_or_404(m.group(1)):
                    return missing("Page")
                items = [pages[i] for i in c[key].get(m.group(1), [])]
                return 200, _link_page(items, q, f"/pages/{m.group(1)}/{suffix}")

        tree("children", "children")
        tree("ancestors", "ancestors")
        tree("descendants", "descendants")

        def listing(suffix: str, key: str) -> None:
            @r("GET", V2 + r"/pages/(\d+)/" + suffix)
            def _l(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
                if not page_or_404(m.group(1)):
                    return missing("Page")
                return 200, _link_page(c[key].get(m.group(1), []), q, f"/pages/{m.group(1)}/{suffix}")

        listing("versions", "versions")
        listing("labels", "labels")
        listing("properties", "properties")
        listing("footer-comments", "footer_comments")
        listing("inline-comments", "inline_comments")
        listing("attachments", "attachments")

        @r("GET", V2 + r"/pages/(\d+)/operations")
        def ops(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return (200, {"operations": c["operations"].get(m.group(1), [])}) if page_or_404(m.group(1)) else missing("Page")

        @r("GET", V2 + r"/attachments/(\w+)")
        def attachment(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            x = next((x for v in c["attachments"].values() for x in v if x["id"] == m.group(1)), None)
            return (200, x) if x else missing("Attachment")

        @r("GET", r"/wiki/download/attachments/(\d+)/([^/]+)")
        def download(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            x = next((x for x in c["attachments"].get(m.group(1), []) if x["title"] == m.group(2)), None)
            return (200, c["attachment_content"][x["id"]]) if x else missing("Attachment")

        @r("GET", V2 + "/tasks")
        def tasks(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            items = [t for t in c["tasks"] if (not q.get("page-id") or t["pageId"] == q["page-id"]) and (not q.get("status") or t["status"] == q["status"])]
            return 200, _link_page(items, q, "/tasks")

        @r("GET", V2 + "/spaces")
        def spaces(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            keys = set(q["keys"].split(",")) if q.get("keys") else None
            items = [x for x in c["spaces"] if (not keys or x["key"] in keys) and (not q.get("type") or x["type"] == q["type"])]
            return 200, _link_page(items, q, "/spaces")

        @r("GET", V2 + r"/spaces/(\d+)")
        def space(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            x = next((x for x in c["spaces"] if x["id"] == m.group(1)), None)
            return (200, x) if x else missing("Space")

        @r("GET", V2 + r"/spaces/(\d+)/permissions")
        def space_perms(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"results": []}

        # ---- Confluence v1 ----
        @r("GET", V1 + "/search")
        def cql(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            if not q.get("cql", "").strip():
                return 400, {"message": "cql must not be empty"}
            start, size = int(q.get("start", 0)), int(q.get("limit", 25))
            chunk = c["search"][start:start + size]
            body: dict[str, Any] = {"results": chunk, "start": start, "limit": size, "size": len(chunk), "totalSize": len(c["search"]), "_links": {}}
            if start + size < len(c["search"]):
                body["_links"]["next"] = f"/rest/api/search?cql={q['cql']}&start={start + size}&limit={size}"
            return 200, body

        @r("GET", V1 + r"/content/(\d+)/restriction/byOperation")
        def restrictions(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return (200, c["restrictions"].get(m.group(1), {})) if page_or_404(m.group(1)) else missing("Page")

        @r("GET", V1 + "/template/page")
        def templates(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"results": [t for t in c["templates"] if not q.get("spaceKey") or t["space"]["key"] == q["spaceKey"]]}

        @r("GET", V1 + r"/template/(\w+)")
        def template(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            x = next((x for x in c["templates"] if x["templateId"] == m.group(1)), None)
            return (200, x) if x else missing("Template")
