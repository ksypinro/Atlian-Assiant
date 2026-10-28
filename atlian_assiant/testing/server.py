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


def _parse_multipart(content: bytes, ctype: str) -> dict[str, tuple[str | None, bytes]]:
    """Minimal multipart/form-data parser: {field: (filename or None, bytes)}."""
    boundary = ctype.split("boundary=")[1].strip('"').encode()
    out: dict[str, tuple[str | None, bytes]] = {}
    for part in content.split(b"--" + boundary):
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        if not part or part.strip() == b"--":
            continue
        head, _, data = part.partition(b"\r\n\r\n")
        disp = head.decode(errors="replace")
        name = re.search(r'name="([^"]*)"', disp)
        fname = re.search(r'filename="([^"]*)"', disp)
        if name:
            out[name.group(1)] = (fname.group(1) if fname else None, data)
    return out


class ScenarioServer:
    def __init__(self, raw: dict[str, Any] | None = None) -> None:
        self.raw = raw or scenario()
        self.requests: list[dict[str, Any]] = []
        self.fail_next: list[tuple[Any, ...]] = []   # (status, headers[, method]) to return before serving normally
        self.routes: list[tuple[str, re.Pattern[str], Handler]] = []
        self._register()
        self._register_writes()
        self.dynamic_webhooks: list[dict[str, Any]] = []
        self.admin_webhooks: list[dict[str, Any]] = []
        self.failed_webhooks: list[dict[str, Any]] = []
        self.dynamic_requires_oauth = False           # True: dynamic registration answers 403 like API-token Basic auth does
        self.automation_calls: list[dict[str, Any]] = []
        self.org_events: list[dict[str, Any]] = [
            {"id": f"ev{i}", "type": "events", "attributes": {"time": f"2026-09-01T10:0{i}:00Z", "action": "user_created" if i % 2 == 0 else "login",
                                                                "actor": {"id": "acc-ann"}, "context": [{"id": "u", "type": "user"}],
                                                                "processedAt": f"2026-09-01T10:0{i}:05Z"}} for i in range(5)]
        self._register_events()
        self._register_org()
        self.notification_schemes: list[dict[str, Any]] = []
        self._register_notifications()

    # ---- plumbing ----
    def route(self, method: str, pattern: str) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            self.routes.append((method, re.compile("^" + pattern + "$"), fn))
            return fn
        return deco

    def __call__(self, request: httpx.Request) -> httpx.Response:
        parts = urlsplit(str(request.url))
        q = dict(parse_qsl(parts.query))
        ctype = request.headers.get("content-type", "")
        body: Any = None
        if request.content and ctype.startswith("application/json"):
            body = json.loads(request.content)
        elif request.content and ctype.startswith("multipart/"):
            body = {"_multipart": _parse_multipart(request.content, ctype)}
        self.requests.append({"method": request.method, "path": parts.path, "query": q, "json": body, "headers": dict(request.headers)})
        if self.fail_next and (len(self.fail_next[0]) < 3 or self.fail_next[0][2] == request.method):
            status, headers = self.fail_next.pop(0)[:2]
            return httpx.Response(status, headers=headers, json={"errorMessages": [f"forced {status}"]})
        routed = re.sub(r"^/ex/(?:jira|confluence)/[^/]+", "", parts.path)         # OAuth / service-account gateway prefix
        for method, pat, fn in self.routes:
            m = pat.match(routed)
            if request.method == method and m:
                status, payload = fn(m, q, body)
                if payload is None:
                    return httpx.Response(status)
                if isinstance(payload, bytes):
                    return httpx.Response(status, content=payload)
                return httpx.Response(status, json=payload)
        return httpx.Response(404, json={"errorMessages": [f"no route {request.method} {parts.path}"]})

    def client(self, **kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self), **kwargs)

    # ---- routes ----
    def _register(self) -> None:
        r, j, a, s, c = self.route, self.raw["jira"], self.raw["agile"], self.raw["jsm"], self.raw["confluence"]
        J, A, S, V2, V1 = r"/rest/api/3", r"/rest/agile/1.0", r"/rest/servicedeskapi", r"/wiki/api/v2", r"/wiki/rest/api"

        def issue_or_404(key: str) -> dict[str, Any] | None:
            found: dict[str, Any] | None = next((i for i in j["issues"] if i["key"] == key.upper()), None)
            return found

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
                items = [x for x in c[key].get(m.group(1), []) if not q.get("key") or x.get("key") == q["key"]]
                return 200, _link_page(items, q, f"/pages/{m.group(1)}/{suffix}")

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


    # ------------------------------------------------------------------------------------------------------------
    # Stateful write routes: they mutate ``self.raw`` so read-after-write works against the same dataset.
    # ------------------------------------------------------------------------------------------------------------
    def _register_writes(self) -> None:
        r, j, a, s, c = self.route, self.raw["jira"], self.raw["agile"], self.raw["jsm"], self.raw["confluence"]
        J, A, S, V2, V1 = r"/rest/api/3", r"/rest/agile/1.0", r"/rest/servicedeskapi", r"/wiki/api/v2", r"/wiki/rest/api"
        counter = {"n": 5000}
        me = j["myself"]

        def nxt() -> int:
            counter["n"] += 1
            return counter["n"]

        def find(key: str) -> dict[str, Any] | None:
            found: dict[str, Any] | None = next((i for i in j["issues"] if i["key"] == key.upper()), None)
            return found

        def missing(what: str) -> tuple[int, Any]:
            return 404, {"errorMessages": [f"{what} does not exist or you do not have permission to see it."], "errors": {}}

        def make_issue(fields: dict[str, Any]) -> dict[str, Any]:
            proj = next((p for p in j["projects"] if p["key"] == fields["project"]["key"]), {"id": "9", "key": fields["project"]["key"], "name": ""})
            n = nxt()
            user = next((u for u in j["users"] if u["accountId"] == (fields.get("assignee") or {}).get("accountId")), None)
            issue: dict[str, Any] = {"id": str(10000 + n), "key": f"{proj['key']}-{n}", "fields": {
                "summary": fields.get("summary"), "status": {"id": "1", "name": "To Do", "statusCategory": {"name": "To Do"}},
                "issuetype": {"name": (fields.get("issuetype") or {}).get("name", "Task")}, "project": {"id": proj["id"], "key": proj["key"], "name": proj.get("name")},
                "assignee": user, "reporter": me, "labels": list(fields.get("labels", [])), "description": fields.get("description"),
                "created": "2026-10-01T10:00:00.000+0000", "updated": "2026-10-01T10:00:00.000+0000", "attachment": [], "issuelinks": [],
                **{k: v for k, v in fields.items() if k.startswith("customfield_")}}}
            if fields.get("parent"):
                issue["fields"]["parent"] = fields["parent"]
            j["issues"].append(issue)
            return issue

        @r("POST", J + "/issue")
        def create_issue(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            f = b["fields"]
            if not str(f.get("summary") or "").strip():
                return 400, {"errorMessages": [], "errors": {"summary": "You must specify a summary of the issue."}}
            i = make_issue(f)
            return 201, {"id": i["id"], "key": i["key"], "self": f"https://example.atlassian.net/rest/api/3/issue/{i['id']}"}

        @r("POST", J + "/issue/bulk")
        def bulk(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            made = [make_issue(u["fields"]) for u in b["issueUpdates"]]
            return 201, {"issues": [{"id": i["id"], "key": i["key"]} for i in made], "errors": []}

        @r("PUT", J + r"/issue/([A-Za-z0-9-]+)")
        def edit(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            i = find(m.group(1))
            if not i:
                return missing("Issue")
            i["fields"].update(b.get("fields", {}))
            for op in (b.get("update") or {}).get("labels", []):
                if "add" in op and op["add"] not in i["fields"]["labels"]:
                    i["fields"]["labels"].append(op["add"])
                if "remove" in op and op["remove"] in i["fields"]["labels"]:
                    i["fields"]["labels"].remove(op["remove"])
            return 204, None

        @r("PUT", J + r"/issue/([A-Za-z0-9-]+)/assignee")
        def assign(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            i = find(m.group(1))
            if not i:
                return missing("Issue")
            i["fields"]["assignee"] = next((u for u in j["users"] if u["accountId"] == b["accountId"]), None) if b["accountId"] else None
            return 204, None

        @r("POST", J + r"/issue/([A-Za-z0-9-]+)/transitions")
        def transition(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            i = find(m.group(1))
            t = next((t for t in j["transitions"].get(m.group(1).upper(), []) if t["id"] == b["transition"]["id"]), None)
            if not i or not t:
                return 400, {"errorMessages": ["Transition is not valid for this issue."], "errors": {}}
            i["fields"]["status"] = t["to"]
            for op in (b.get("update") or {}).get("comment", []):
                j["comments"].setdefault(i["key"], []).append({"id": f"c{nxt()}", "author": me, "body": op["add"]["body"], "created": "2026-10-01T10:00:00.000+0000"})
            return 204, None

        @r("POST", J + r"/issue/([A-Za-z0-9-]+)/comment")
        def add_comment(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            if not find(m.group(1)):
                return missing("Issue")
            cm = {"id": f"c{nxt()}", "author": me, "body": b["body"], "created": "2026-10-01T10:00:00.000+0000", "updated": "2026-10-01T10:00:00.000+0000",
                  **({"visibility": b["visibility"]} if b.get("visibility") else {})}
            j["comments"].setdefault(m.group(1).upper(), []).append(cm)
            return 201, cm

        @r("PUT", J + r"/issue/([A-Za-z0-9-]+)/comment/(\w+)")
        def edit_comment(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            cm = next((x for x in j["comments"].get(m.group(1).upper(), []) if x["id"] == m.group(2)), None)
            if not cm:
                return missing("Comment")
            cm["body"] = b["body"]
            return 200, cm

        @r("DELETE", J + r"/issue/([A-Za-z0-9-]+)/comment/(\w+)")
        def del_comment(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            items = j["comments"].get(m.group(1).upper(), [])
            if not any(x["id"] == m.group(2) for x in items):
                return missing("Comment")
            j["comments"][m.group(1).upper()] = [x for x in items if x["id"] != m.group(2)]
            return 204, None

        @r("POST", J + r"/issue/([A-Za-z0-9-]+)/worklog")
        def worklog(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            if not find(m.group(1)):
                return missing("Issue")
            w = {"id": f"w{nxt()}", "author": me, "timeSpent": b["timeSpent"], "timeSpentSeconds": 3600, "started": b.get("started", "2026-10-01T09:00:00.000+0000"),
                 **({"comment": b["comment"]} if b.get("comment") else {})}
            j["worklogs"].setdefault(m.group(1).upper(), []).append(w)
            return 201, w

        @r("POST", J + "/issueLink")
        def link(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            i, o = find(b["inwardIssue"]["key"]), find(b["outwardIssue"]["key"])
            if not i or not o:
                return missing("Issue")
            i["fields"]["issuelinks"].append({"id": f"l{nxt()}", "type": {"name": b["type"]["name"], "outward": b["type"]["name"].lower(), "inward": "inward"},
                                              "outwardIssue": {"key": o["key"], "fields": {"summary": o["fields"]["summary"]}}})
            return 201, None

        @r("POST", J + r"/issue/([A-Za-z0-9-]+)/remotelink")
        def remote(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            if not find(m.group(1)):
                return missing("Issue")
            rl = {"id": nxt(), "object": b["object"]}
            j["remote_links"].setdefault(m.group(1).upper(), []).append(rl)
            return 201, rl

        @r("POST", J + r"/issue/([A-Za-z0-9-]+)/attachments")
        def attach(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            i = find(m.group(1))
            if not i:
                return missing("Issue")
            fname, data = b["_multipart"]["file"]
            att = {"id": f"att{nxt()}", "filename": fname, "size": len(data), "mimeType": "text/plain", "author": me, "created": "2026-10-01T10:00:00.000+0000"}
            i["fields"]["attachment"].append(att)
            j["attachment_content"][att["id"]] = data
            return 200, [att]

        @r("POST", J + r"/issue/([A-Za-z0-9-]+)/watchers")
        def watch(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            w = j["watchers"].setdefault(m.group(1).upper(), [])
            u = next((u for u in j["users"] if u["accountId"] == b), None)
            if u and u not in w:
                w.append(u)
            return 204, None

        @r("DELETE", J + r"/issue/([A-Za-z0-9-]+)/watchers")
        def unwatch(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            j["watchers"][m.group(1).upper()] = [u for u in j["watchers"].get(m.group(1).upper(), []) if u["accountId"] != q.get("accountId")]
            return 204, None

        @r("PUT", J + r"/issue/([A-Za-z0-9-]+)/properties/([^/]+)")
        def set_prop(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            j["properties"].setdefault(m.group(1).upper(), {})[m.group(2)] = {"key": m.group(2), "value": b}
            return 200, None

        @r("DELETE", J + r"/issue/([A-Za-z0-9-]+)")
        def delete_issue(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            if not find(m.group(1)):
                return missing("Issue")
            j["issues"] = [i for i in j["issues"] if i["key"] != m.group(1).upper()]
            return 204, None

        @r("DELETE", J + r"/attachment/(\w+)")
        def delete_att(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            if m.group(1) not in j["attachment_content"]:
                return missing("Attachment")
            del j["attachment_content"][m.group(1)]
            for i in j["issues"]:
                i["fields"]["attachment"] = [x for x in i["fields"].get("attachment", []) if x["id"] != m.group(1)]
            return 204, None

        @r("POST", J + "/version")
        def create_version(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            proj = next((p for p in j["projects"] if int(p["id"]) == b["projectId"]), None)
            v = {"id": f"v{nxt()}", "name": b["name"], "released": False, "releaseDate": b.get("releaseDate")}
            j["versions"].setdefault(proj["key"] if proj else "?", []).append(v)
            return 201, v

        @r("PUT", J + r"/version/(\w+)")
        def update_version(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            for vs in j["versions"].values():
                for v in vs:
                    if v["id"] == m.group(1):
                        v.update(b)
                        return 200, v
            return missing("Version")

        @r("DELETE", J + r"/version/(\w+)")
        def delete_version(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            for k, vs in j["versions"].items():
                if any(v["id"] == m.group(1) for v in vs):
                    j["versions"][k] = [v for v in vs if v["id"] != m.group(1)]
                    return 204, None
            return missing("Version")

        @r("POST", J + "/project")
        def create_project(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            p = {"id": str(nxt()), "key": b["key"], "name": b["name"], "projectTypeKey": b.get("projectTypeKey")}
            j["projects"].append(p)
            return 201, p

        @r("PUT", J + r"/project/([A-Z]+)")
        def update_project(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            p = next((p for p in j["projects"] if p["key"] == m.group(1)), None)
            if not p:
                return missing("Project")
            p.update(b)
            return 200, p

        @r("GET", J + "/workflow/search")
        def workflows(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"values": [{"id": {"entityId": "wf1", "name": "Software workflow"}, "statuses": j["statuses"]}]}

        @r("GET", J + "/screens")
        def screens(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"values": [{"id": 1, "name": "Default screen"}]}

        @r("PUT", J + r"/screens/(\d+)")
        def update_screen(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"id": int(m.group(1)), **b}

        # ---- agile ----
        @r("POST", A + "/sprint")
        def create_sprint(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            sp = {"id": nxt(), "name": b["name"], "state": "future", "goal": b.get("goal")}
            a["sprints"].setdefault(str(b["originBoardId"]), []).append(sp)
            return 201, sp

        @r("POST", A + r"/sprint/(\d+)")
        def update_sprint(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            for v in a["sprints"].values():
                for sp in v:
                    if str(sp["id"]) == m.group(1):
                        sp.update(b)
                        return 200, sp
            return missing("Sprint")

        @r("DELETE", A + r"/sprint/(\d+)")
        def delete_sprint(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            for k, v in a["sprints"].items():
                if any(str(sp["id"]) == m.group(1) for sp in v):
                    a["sprints"][k] = [sp for sp in v if str(sp["id"]) != m.group(1)]
                    return 204, None
            return missing("Sprint")

        @r("POST", A + r"/sprint/(\d+)/issue")
        def to_sprint(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            a["sprint_issues"].setdefault(m.group(1), []).extend(i for i in (find(k) for k in b["issues"]) if i)
            return 204, None

        @r("POST", A + "/backlog/issue")
        def to_backlog(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 204, None

        @r("PUT", A + "/issue/rank")
        def rank(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 204, None

        @r("POST", A + "/board")
        def create_board(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            board = {"id": nxt(), "name": b["name"], "type": b["type"]}
            a["boards"].append(board)
            return 201, board

        # ---- JSM ----
        @r("POST", S + "/request")
        def create_request(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            key = f"OPS-{nxt()}"
            req = {"issueId": str(nxt()), "issueKey": key, "requestTypeId": b["requestTypeId"], "serviceDeskId": b["serviceDeskId"], "currentStatus": {"status": "Open"},
                   "reporter": me, "createdDate": {"iso8601": "2026-10-01T10:00:00+0000"},
                   "requestFieldValues": [{"fieldId": k, "value": v} for k, v in b["requestFieldValues"].items()]}
            s["requests"].append(req)
            return 201, req

        @r("POST", S + r"/request/([A-Z]+-\d+)/comment")
        def req_comment(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            if not any(x["issueKey"] == m.group(1) for x in s["requests"]):
                return missing("Request")
            cm = {"id": f"rc{nxt()}", "author": me, "body": b["body"], "public": b["public"], "created": {"iso8601": "2026-10-01T10:00:00+0000"}}
            s["comments"].setdefault(m.group(1), []).append(cm)
            return 201, cm

        @r("GET", S + r"/request/([A-Z]+-\d+)/transition")
        def req_transitions(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"values": [{"id": "711", "name": "Resolve this issue"}]}

        @r("POST", S + r"/request/([A-Z]+-\d+)/transition")
        def req_transition(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            for x in s["requests"]:
                if x["issueKey"] == m.group(1):
                    x["currentStatus"] = {"status": "Resolved"}
                    return 204, None
            return missing("Request")

        @r("POST", S + r"/request/([A-Z]+-\d+)/approval/(\w+)")
        def approval(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"id": m.group(2), "finalDecision": b["decision"]}

        @r("POST", S + r"/servicedesk/(\d+)/customer")
        def add_customers(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            s["customers"].setdefault(m.group(1), []).extend({"accountId": x, "displayName": x} for x in b["accountIds"])
            return 204, None

        # ---- Confluence v2 ----
        pages = c["pages"]
        coll = {"pages": "page", "blogposts": "blogpost", "folders": "folder"}

        def new_content(kind: str, b: Any) -> dict[str, Any]:
            cid = str(nxt())
            page = {"id": cid, "status": b.get("status", "current"), "title": b["title"], "spaceId": b.get("spaceId"), "parentId": b.get("parentId"), "authorId": "acc-ann",
                    "createdAt": "2026-10-01T10:00:00.000Z", "version": {"number": 1, "createdAt": "2026-10-01T10:00:00.000Z"},
                    "body": {"storage": {"value": (b.get("body") or {}).get("value", "")}}, "_links": {"webui": f"/spaces/ENG/pages/{cid}"}}
            pages[cid] = page
            c["versions"][cid] = [{"number": 1, "authorId": "acc-ann", "createdAt": "2026-10-01T10:00:00.000Z"}]
            if b.get("parentId"):
                c["children"].setdefault(str(b["parentId"]), []).append(cid)
            return page

        for path, kind in coll.items():
            self.routes.append(("POST", re.compile(f"^{V2}/{path}$"), (lambda k: lambda m, q, b: (200, new_content(k, b)))(kind)))

        @r("PUT", V2 + r"/pages/(\d+)")
        def update_page(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            p = pages.get(m.group(1))
            if not p:
                return missing("Page")
            if b["version"]["number"] != p["version"]["number"] + 1:
                return 409, {"message": f"Version must be incremented on update. Current Version: {p['version']['number']}", "statusCode": 409}
            c["page_versions"].setdefault(m.group(1), {})[str(p["version"]["number"])] = p["body"]["storage"]["value"]
            p["title"] = b["title"]
            p["body"] = {"storage": {"value": b["body"]["value"]}}
            p["version"] = {"number": b["version"]["number"], "createdAt": "2026-10-02T10:00:00.000Z", "message": b["version"].get("message")}
            c["versions"][m.group(1)].insert(0, {"number": b["version"]["number"], "message": b["version"].get("message"), "authorId": "acc-ann", "createdAt": "2026-10-02T10:00:00.000Z"})
            return 200, p

        @r("DELETE", V2 + r"/pages/(\d+)")
        def delete_page(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            if m.group(1) not in pages:
                return missing("Page")
            del pages[m.group(1)]
            return 204, None

        @r("POST", V2 + "/footer-comments")
        def add_footer(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            cm = {"id": f"fc{nxt()}", "body": {"storage": {"value": b["body"]["value"]}}, "version": {"number": 1, "authorId": "acc-ann", "createdAt": "2026-10-01T10:00:00.000Z"}}
            c["footer_comments"].setdefault(b["pageId"], []).append(cm)
            return 200, cm

        @r("POST", V2 + "/inline-comments")
        def add_inline(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            cm = {"id": f"ic{nxt()}", "body": {"storage": {"value": b["body"]["value"]}}, "resolutionStatus": "open",
                  "version": {"number": 1, "authorId": "acc-ann", "createdAt": "2026-10-01T10:00:00.000Z"}}
            c["inline_comments"].setdefault(b["pageId"], []).append(cm)
            return 200, cm

        def comment_store(kind: str) -> dict[str, list[dict[str, Any]]]:
            store: dict[str, list[dict[str, Any]]] = c["footer_comments"] if kind == "footer" else c["inline_comments"]
            return store

        def find_comment(kind: str, cid: str) -> dict[str, Any] | None:
            for items in comment_store(kind).values():
                for x in items:
                    if x["id"] == cid:
                        return x
            return None

        for kind in ("footer", "inline"):
            def get_c(m: re.Match[str], q: dict[str, str], b: Any, kind: str = kind) -> tuple[int, Any]:
                x = find_comment(kind, m.group(1))
                return (200, x) if x else missing("Comment")

            def put_c(m: re.Match[str], q: dict[str, str], b: Any, kind: str = kind) -> tuple[int, Any]:
                x = find_comment(kind, m.group(1))
                if not x:
                    return missing("Comment")
                x["body"] = {"storage": {"value": b["body"]["value"]}}
                x["version"]["number"] = b["version"]["number"]
                if "resolved" in b:
                    x["resolutionStatus"] = "resolved" if b["resolved"] else "open"
                return 200, x

            def del_c(m: re.Match[str], q: dict[str, str], b: Any, kind: str = kind) -> tuple[int, Any]:
                if not find_comment(kind, m.group(1)):
                    return missing("Comment")
                for k, items in comment_store(kind).items():
                    comment_store(kind)[k] = [x for x in items if x["id"] != m.group(1)]
                return 204, None

            self.routes.append(("GET", re.compile(f"^{V2}/{kind}-comments/(\\w+)$"), get_c))
            self.routes.append(("PUT", re.compile(f"^{V2}/{kind}-comments/(\\w+)$"), put_c))
            self.routes.append(("DELETE", re.compile(f"^{V2}/{kind}-comments/(\\w+)$"), del_c))

        @r("DELETE", V2 + r"/attachments/(\w+)")
        def del_attachment(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            if m.group(1) not in c["attachment_content"]:
                return missing("Attachment")
            del c["attachment_content"][m.group(1)]
            for k, v in c["attachments"].items():
                c["attachments"][k] = [x for x in v if x["id"] != m.group(1)]
            return 204, None

        @r("POST", V2 + r"/pages/(\d+)/properties")
        def add_prop(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            p = {"id": f"p{nxt()}", "key": b["key"], "value": b["value"], "version": {"number": 1}}
            c["properties"].setdefault(m.group(1), []).append(p)
            return 200, p

        @r("PUT", V2 + r"/pages/(\d+)/properties/(\w+)")
        def put_prop(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            for p in c["properties"].get(m.group(1), []):
                if p["id"] == m.group(2):
                    p["value"], p["version"] = b["value"], b["version"]
                    return 200, p
            return missing("Property")

        @r("PUT", V2 + r"/tasks/(\w+)")
        def put_task(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            for t in c["tasks"]:
                if t["id"] == m.group(1):
                    t["status"] = b["status"]
                    return 200, t
            return missing("Task")

        # ---- Confluence v1 ----
        @r("PUT", V1 + r"/content/(\d+)/move/(\w+)/(\d+)")
        def move(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            p = pages.get(m.group(1))
            if not p or m.group(3) not in pages:
                return missing("Page")
            p["parentId"] = m.group(3)
            return 200, {"id": m.group(1)}

        @r("POST", V1 + r"/content/(\d+)/copy")
        def copy(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            src = pages.get(m.group(1))
            if not src:
                return missing("Page")
            page = new_content("page", {"title": b.get("pageTitle") or f"Copy of {src['title']}", "spaceId": src["spaceId"], "parentId": b["destination"]["value"],
                                        "body": {"value": src["body"]["storage"]["value"]}})
            return 200, {"id": page["id"]}

        @r("POST", V1 + "/content/archive")
        def archive(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            for x in b["pages"]:
                if x["id"] not in pages:
                    return missing("Page")
                pages[x["id"]]["status"] = "archived"
            return 202, {"id": "task1"}

        @r("POST", V1 + r"/content/(\d+)/version")
        def restore(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            p = pages.get(m.group(1))
            if not p:
                return missing("Page")
            html = c["page_versions"].get(m.group(1), {}).get(str(b["params"]["versionNumber"]))
            if html is None:
                return missing("Version")
            p["body"] = {"storage": {"value": html}}
            p["version"] = {"number": p["version"]["number"] + 1, "createdAt": "2026-10-02T10:00:00.000Z", "message": b["params"].get("message")}
            return 200, {"id": m.group(1)}

        @r("POST", V1 + r"/content/(\d+)/label")
        def add_label(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            if m.group(1) not in pages:
                return missing("Page")
            labels = c["labels"].setdefault(m.group(1), [])
            for x in b:
                if x["name"] not in {lb["name"] for lb in labels}:
                    labels.append({"id": str(nxt()), "name": x["name"], "prefix": x["prefix"]})
            return 200, {"results": labels}

        @r("DELETE", V1 + r"/content/(\d+)/label/(\w+)")
        def del_label(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            c["labels"][m.group(1)] = [x for x in c["labels"].get(m.group(1), []) if x["name"] != m.group(2)]
            return 204, None

        @r("POST", V1 + r"/content/(\d+)/child/attachment")
        def conf_attach(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            if m.group(1) not in pages:
                return missing("Page")
            fname, data = b["_multipart"]["file"]
            att = {"id": f"cat{nxt()}", "title": fname, "mediaType": "text/plain", "fileSize": len(data), "pageId": m.group(1),
                   "downloadLink": f"/download/attachments/{m.group(1)}/{fname}", "version": {"number": 1}}
            c["attachments"].setdefault(m.group(1), []).append(att)
            c["attachment_content"][att["id"]] = data
            return 200, {"results": [{"id": att["id"], "title": fname, "extensions": {"mediaType": "text/plain", "fileSize": len(data)}}]}

        @r("PUT", V1 + r"/content/(\d+)/restriction")
        def restriction(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            if m.group(1) not in pages:
                return missing("Page")
            out = {}
            for side in b:
                out[side["operation"]] = {"operation": side["operation"], "restrictions": {
                    "user": {"results": [{"accountId": u["accountId"], "displayName": u["accountId"]} for u in side["restrictions"]["user"]]},
                    "group": {"results": [{"name": g["name"]} for g in side["restrictions"]["group"]]}}}
            c["restrictions"][m.group(1)] = out
            return 200, {"results": list(out.values())}

        @r("POST", V1 + "/space")
        def create_space(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            sp = {"id": str(nxt()), "key": b["key"], "name": b["name"], "type": "global", "status": "current"}
            c["spaces"].append(sp)
            return 200, {"key": b["key"]}

        @r("POST", V1 + r"/user/watch/(content|space|label)/(\w+)")
        def watch_conf(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 204, None

        @r("DELETE", V1 + r"/user/watch/(content|space|label)/(\w+)")
        def unwatch_conf(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 204, None

    def _register_events(self) -> None:
        """Webhook management (dynamic + admin families) and the Automation incoming-webhook endpoint."""
        r = self.route
        J, ADMIN = r"/rest/api/3", r"/rest/webhooks/1.0"
        ids = {"n": 700}
        stamp = "2026-12-01T00:00:00.000+0000"

        def nxt() -> int:
            ids["n"] += 1
            return ids["n"]

        @r("GET", J + "/webhook")
        def dyn_list(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"values": list(self.dynamic_webhooks), "isLast": True, "startAt": 0, "maxResults": 100, "total": len(self.dynamic_webhooks)}

        @r("POST", J + "/webhook")
        def dyn_register(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            if self.dynamic_requires_oauth:
                return 403, {"errorMessages": ["Only Connect and OAuth 2.0 apps can register dynamic webhooks"]}
            results: list[dict[str, Any]] = []
            for hook in b["webhooks"]:
                if any(e.startswith("bogus") for e in hook["events"]):
                    results.append({"errors": [f"unknown event {e}" for e in hook["events"] if e.startswith("bogus")]})
                    continue
                wid = nxt()
                self.dynamic_webhooks.append({"id": wid, "jqlFilter": hook.get("jqlFilter", ""), "fieldIdsFilter": hook.get("fieldIdsFilter", []),
                                              "events": hook["events"], "expirationDate": stamp, "url": b["url"]})
                results.append({"createdWebhookId": wid})
            return 202, {"webhookRegistrationResult": results}

        @r("DELETE", J + "/webhook")
        def dyn_delete(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            gone = set(b["webhookIds"])
            self.dynamic_webhooks[:] = [w for w in self.dynamic_webhooks if w["id"] not in gone]
            return 202, None

        @r("PUT", J + "/webhook/refresh")
        def dyn_refresh(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"expirationDate": "2027-01-01T00:00:00.000+0000"}

        @r("GET", J + "/webhook/failed")
        def dyn_failed(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"values": list(self.failed_webhooks), "maxResults": 100}

        @r("GET", ADMIN + "/webhook")
        def admin_list(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, list(self.admin_webhooks)

        @r("POST", ADMIN + "/webhook")
        def admin_register(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            wid = nxt()
            hook = {**b, "enabled": True, "self": f"https://example.atlassian.net/rest/webhooks/1.0/webhook/{wid}"}
            self.admin_webhooks.append(hook)
            return 201, hook

        @r("PUT", ADMIN + r"/webhook/(\d+)")
        def admin_update(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            hook = {**b, "enabled": True, "self": f"https://example.atlassian.net/rest/webhooks/1.0/webhook/{m.group(1)}"}
            self.admin_webhooks[:] = [hook if str(w["self"]).endswith("/" + m.group(1)) else w for w in self.admin_webhooks]
            return 200, hook

        @r("DELETE", ADMIN + r"/webhook/(\d+)")
        def admin_delete(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            self.admin_webhooks[:] = [w for w in self.admin_webhooks if not str(w["self"]).endswith("/" + m.group(1))]
            return 204, None

        @r("POST", r"/pg/webhooks/[\w-]+")
        def automation_incoming(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            self.automation_calls.append({"path": m.group(0), "body": b})
            return 200, {"message": "Webhook triggered"}

    def _register_org(self) -> None:
        """Organization audit API: /events (windowed), /events/{id}, /event-actions, /events-stream (cursor kept on the last page)."""
        r = self.route
        ORG = r"/admin/v1/orgs/([\w-]+)"

        def page(q: dict[str, str], items: list[dict[str, Any]], always_cursor: bool) -> dict[str, Any]:
            start, limit = int(q.get("cursor") or 0), min(int(q.get("limit", "200")), 500)
            chunk = items[start:start + limit]
            nxt = start + limit if start + limit < len(items) else (start + len(chunk) if always_cursor else None)
            body: dict[str, Any] = {"data": chunk, "meta": {"page_size": limit}, "links": {}}
            if nxt is not None:
                body["meta"]["next"] = str(nxt)
                body["links"]["next"] = f"https://api.atlassian.com/admin/v1/orgs/org-1/events?cursor={nxt}"
            return body

        @r("GET", ORG + "/events")
        def events(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            items = [e for e in self.org_events if (not q.get("action") or e["attributes"]["action"] == q["action"])
                     and (not q.get("q") or q["q"] in json.dumps(e))]
            return 200, page(q, items, False)

        @r("GET", ORG + r"/events/([\w-]+)")
        def one(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            found = next((e for e in self.org_events if e["id"] == m.group(2)), None)
            return (200, {"data": found}) if found else (404, {"errors": [{"title": "not found"}]})

        @r("GET", ORG + "/event-actions")
        def actions(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"data": {"actions": sorted({e["attributes"]["action"] for e in self.org_events})}}

        @r("GET", ORG + "/events-stream")
        def stream(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            items = self.org_events if q.get("sortOrder", "asc") == "asc" else list(reversed(self.org_events))
            return 200, page(q, items, True)

    def _register_notifications(self) -> None:
        """Notification schemes, events and filters (DM-25, DM-26, DM-29 provisioners)."""
        r = self.route
        J = r"/rest/api/3"
        self.filters: list[dict[str, Any]] = self.raw["jira"]["filters"]          # shared with the read routes above
        events = [{"id": 1, "name": "Issue Created"}, {"id": 2, "name": "Issue Updated"}, {"id": 3, "name": "Issue Commented"}, {"id": 4, "name": "Issue Assigned"}]
        ids = {"n": 900}

        @r("GET", J + "/events")
        def list_events(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, events

        @r("GET", J + "/notificationscheme")
        def schemes(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            name = q.get("query")
            return 200, {"values": [s for s in self.notification_schemes if not name or s["name"] == name], "isLast": True}

        @r("POST", J + "/notificationscheme")
        def create_scheme(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            ids["n"] += 1
            scheme = {"id": str(ids["n"]), "name": b["name"], "notificationSchemeEvents": b.get("notificationSchemeEvents", [])}
            self.notification_schemes.append(scheme)
            return 201, {"id": scheme["id"]}

        @r("PUT", J + r"/notificationscheme/(\d+)/notification")
        def add_notifications(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            for s in self.notification_schemes:
                if s["id"] == m.group(1):
                    s["notificationSchemeEvents"] = b["notificationSchemeEvents"]
            return 204, None

        @r("DELETE", J + r"/notificationscheme/(\d+)")
        def delete_scheme(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            self.notification_schemes[:] = [s for s in self.notification_schemes if s["id"] != m.group(1)]
            return 204, None

        @r("PUT", J + r"/project/(\w+)")
        def project_update(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"key": m.group(1), **b}

        @r("POST", J + "/filter")
        def filter_create(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            ids["n"] += 1
            f = {"id": str(ids["n"]), **b}
            self.filters.append(f)
            return 200, f

        @r("DELETE", J + r"/filter/(\d+)")
        def filter_delete(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            self.filters[:] = [f for f in self.filters if f["id"] != m.group(1)]
            return 204, None
