"""A Data Center flavoured mock: the same scenario dataset served the way Jira / Confluence DATA CENTER serve it.

Jira DC (`/rest/api/2`) is the Cloud dataset with DC wire differences applied (wiki-markup text instead of ADF, ``name``/``key`` instead of ``accountId``,
offset ``/search``, plain-array projects/filters, ``/secure/attachment`` content). Confluence DC serves ``/rest/api/content`` (v1, expand-based).
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx

from ..formats.adf import adf_to_markdown, markdown_to_adf
from ..formats.wiki import markdown_to_wiki, wiki_to_markdown
from .server import ScenarioServer

Handler = Callable[[re.Match[str], dict[str, str], Any], tuple[int, Any]]


def _name(user: dict[str, Any]) -> str:
    return str(user.get("emailAddress") or user["accountId"]).split("@")[0]


def to_dc(obj: Any) -> Any:
    """Cloud JSON -> Data Center JSON: ADF documents become wiki text; accountId becomes name/key."""
    if isinstance(obj, list):
        return [to_dc(i) for i in obj]
    if isinstance(obj, dict):
        if obj.get("type") == "doc" and "content" in obj:
            return markdown_to_wiki(adf_to_markdown(obj))
        out = {k: to_dc(v) for k, v in obj.items()}
        if "accountId" in out:
            name = _name(obj)
            out.pop("accountId")
            out.update({"name": name, "key": f"JIRAUSER-{name}"})
        return out
    return obj


def from_dc(obj: Any, key: str = "") -> Any:
    """DC request JSON -> what the Cloud handlers understand: wiki text bodies become ADF, {"name"} user refs become {"accountId"}."""
    if isinstance(obj, list):
        return [from_dc(i, key) for i in obj]
    if isinstance(obj, dict):
        if set(obj) == {"name"} and key in ("", "assignee", "reporter", "lead", "user", "author", "creator"):
            return {"accountId": f"acc-{obj['name']}"}
        return {k: from_dc(v, k) for k, v in obj.items()}
    if isinstance(obj, str) and key in ("description", "body", "comment"):
        return markdown_to_adf(wiki_to_markdown(obj))
    return obj


class DcScenarioServer(ScenarioServer):
    def __init__(self, raw: dict[str, Any] | None = None) -> None:
        super().__init__(raw)
        self.dc_requests: list[dict[str, Any]] = []
        self.dc_routes: list[tuple[str, re.Pattern[str], Handler]] = []
        self._dc_register()

    def dc(self, method: str, pattern: str) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            self.dc_routes.append((method, re.compile("^" + pattern + "$"), fn))
            return fn
        return deco

    # ------------------------------------------------------------------------------------------------------ dispatch
    def __call__(self, request: httpx.Request) -> httpx.Response:
        parts = urlsplit(str(request.url))
        q = dict(parse_qsl(parts.query))
        body = json.loads(request.content) if request.content and request.headers.get("content-type", "").startswith("application/json") else None
        self.dc_requests.append({"method": request.method, "path": parts.path, "query": q, "json": body, "headers": dict(request.headers)})
        for method, pat, fn in self.dc_routes:
            m = pat.match(parts.path)
            if request.method == method and m:
                status, payload = fn(m, q, body)
                if payload is None:
                    return httpx.Response(status)
                return httpx.Response(status, content=payload) if isinstance(payload, bytes) else httpx.Response(status, json=payload)
        if parts.path.startswith("/rest/api/2/"):
            new_url = str(request.url).replace("/rest/api/2/", "/rest/api/3/", 1)
            content = json.dumps(from_dc(body)).encode() if body is not None else request.content
            forwarded = httpx.Request(request.method, new_url, headers={k: v for k, v in request.headers.items() if k.lower() != "content-length"}, content=content)
            resp = super().__call__(forwarded)
            if resp.status_code < 300 and resp.headers.get("content-type", "").startswith("application/json") and resp.content:
                return httpx.Response(resp.status_code, json=to_dc(resp.json()))
            return resp
        return super().__call__(request)

    # ------------------------------------------------------------------------------------------------------ routes
    def _dc_register(self) -> None:
        j, c = self.raw["jira"], self.raw["confluence"]
        d = self.dc
        J, V1 = r"/rest/api/2", r"/rest/api"
        users = j["users"]

        def paged(items: list[Any], q: dict[str, str], path: str, **extra: Any) -> dict[str, Any]:
            start, size = int(q.get("start", 0)), int(q.get("limit", 25))
            chunk = items[start:start + size]
            body: dict[str, Any] = {"results": chunk, "start": start, "limit": size, "size": len(chunk), "totalSize": len(items), "_links": {}, **extra}
            if start + size < len(items):
                body["_links"]["next"] = f"{path}?start={start + size}&limit={size}"
            return body

        # ---- Jira DC specifics
        @d("POST", J + "/search")
        def search(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            if not b or not b.get("jql", "").strip():
                return 400, {"errorMessages": ["The JQL query is empty."], "errors": {}}
            start, size = int(b.get("startAt", 0)), int(b.get("maxResults", 50))
            chunk = [to_dc(i) for i in j["issues"][start:start + size]]
            return 200, {"startAt": start, "maxResults": size, "total": len(j["issues"]), "issues": chunk}

        @d("GET", J + "/user")
        def user(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            u = next((x for x in users if _name(x) == q.get("username")), None)
            return (200, to_dc(u)) if u else (404, {"errorMessages": ["The specified user does not exist."]})

        @d("GET", J + "/user/search")
        def user_search(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            t = q.get("username", "").lower()
            return 200, [to_dc(u) for u in users if t in u["displayName"].lower() or t in (u.get("emailAddress") or "").lower() or t in _name(u)]

        @d("GET", J + "/user/assignable/search")
        def assignable(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, [to_dc(u) for u in users]

        @d("GET", J + "/project")
        def projects(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, j["projects"]

        @d("GET", J + "/filter/favourite")
        def filters(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, [to_dc(f) for f in j["filters"]]

        @d("GET", J + "/dashboard")
        def dashboards(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"dashboards": j["dashboards"], "startAt": 0, "maxResults": 50, "total": len(j["dashboards"])}

        @d("GET", J + "/issue/createmeta")
        def createmeta(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            fields = j["create_fields"].get(f"{q.get('projectKeys')}/{q.get('issuetypeIds')}", [])
            return 200, {"projects": [{"key": q.get("projectKeys"), "issuetypes": [{"id": q.get("issuetypeIds"), "fields": {
                f["fieldId"]: {"name": f["name"], "required": f["required"], "schema": f["schema"]} for f in fields}}]}]}

        @d("GET", J + "/workflow")
        def workflows(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, [{"name": "Software workflow", "description": "default", "steps": 4}]

        @d("GET", J + r"/attachment/([^/]+)")
        def att_meta(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            for i in j["issues"]:
                for x in i["fields"].get("attachment", []):
                    if x["id"] == m.group(1):
                        return 200, {**x, "content": f"https://jira.corp.example/secure/attachment/{x['id']}/{x['filename']}"}
            return 404, {"errorMessages": ["Attachment does not exist."]}

        @d("GET", r"/secure/attachment/([^/]+)/([^/]+)")
        def att_content(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            data = j["attachment_content"].get(m.group(1))
            return (200, data) if data is not None else (404, {"errorMessages": ["Attachment does not exist."]})

        @d("GET", J + "/jql/autocompletedata")
        def autocomplete(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"visibleFieldNames": [{"value": "project"}, {"value": "status"}], "visibleFunctionNames": [{"value": "currentUser()"}]}

        @d("GET", J + "/jql/autocompletedata/suggestions")
        def suggestions(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"results": [{"value": "PROJ", "displayName": "Project"}]}

        # ---- Confluence DC (v1)
        pages = c["pages"]
        space_by_id = {s["id"]: s for s in c["spaces"]}

        def dc_page(p: dict[str, Any], expand: str = "") -> dict[str, Any]:
            space = space_by_id[p["spaceId"]]
            out: dict[str, Any] = {"id": p["id"], "type": "page", "status": p["status"], "title": p["title"], "_links": {"webui": p["_links"]["webui"]}}
            wanted = set(expand.split(","))
            if "space" in wanted:
                out["space"] = {"id": space["id"], "key": space["key"], "name": space["name"]}
            if "version" in wanted:
                out["version"] = {"number": p["version"]["number"], "when": p["version"]["createdAt"], "by": {"userKey": p.get("authorId", "")}, "message": p["version"].get("message")}
            if "history" in wanted:
                out["history"] = {"createdDate": p.get("createdAt")}
            if "ancestors" in wanted:
                out["ancestors"] = [{"id": a, "type": "page"} for a in c["ancestors"].get(p["id"], [])]
            if "body.storage" in wanted:
                out["body"] = {"storage": {"value": p["body"]["storage"]["value"], "representation": "storage"}}
            if "body.view" in wanted:
                out["body"] = {"view": {"value": p["body"]["storage"]["value"]}}
            return out

        @d("GET", V1 + r"/content/([^/]+)")
        def content_get(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            p = pages.get(m.group(1))
            attachment = next((x for v in c["attachments"].values() for x in v if x["id"] == m.group(1)), None)
            if attachment:
                return 200, {"id": attachment["id"], "type": "attachment", "title": attachment["title"], "version": {"number": 1},
                             "extensions": {"mediaType": attachment["mediaType"], "fileSize": attachment["fileSize"]}, "_links": {"download": attachment["downloadLink"]}}
            if not p:
                return 404, {"message": "No content found with id : " + m.group(1)}
            if q.get("version") and int(q["version"]) != p["version"]["number"]:
                html = c["page_versions"].get(m.group(1), {}).get(q["version"])
                if html is None:
                    return 404, {"message": "version not found"}
                out = dc_page({**p, "version": {**p["version"], "number": int(q["version"])}, "body": {"storage": {"value": html}}}, q.get("expand", ""))
                return 200, out
            return 200, dc_page(p, q.get("expand", ""))

        @d("GET", V1 + "/content")
        def content_list(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            items = [p for p in pages.values() if (not q.get("spaceKey") or space_by_id[p["spaceId"]]["key"] == q["spaceKey"]) and (not q.get("title") or p["title"] == q["title"])]
            return 200, paged([dc_page(p, q.get("expand", "")) for p in items], q, V1 + "/content")

        @d("GET", V1 + r"/content/([^/]+)/child/page")
        def children(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return (200, paged([dc_page(pages[i], q.get("expand", "")) for i in c["children"].get(m.group(1), [])], q, V1 + f"/content/{m.group(1)}/child/page")) if m.group(1) in pages \
                else (404, {"message": "not found"})

        @d("GET", V1 + r"/content/([^/]+)/descendant/page")
        def descendants(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, paged([dc_page(pages[i], q.get("expand", "")) for i in c["descendants"].get(m.group(1), [])], q, V1 + f"/content/{m.group(1)}/descendant/page")

        @d("GET", V1 + r"/content/([^/]+)/version")
        def versions(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            vs = [{"number": v["number"], "message": v.get("message"), "when": v["createdAt"], "by": {"userKey": v["authorId"]}} for v in c["versions"].get(m.group(1), [])]
            return 200, paged(vs, q, V1 + f"/content/{m.group(1)}/version")

        @d("GET", V1 + r"/content/([^/]+)/label")
        def labels(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"results": c["labels"].get(m.group(1), []), "size": len(c["labels"].get(m.group(1), []))}

        @d("GET", V1 + r"/content/([^/]+)/property")
        def properties(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"results": c["properties"].get(m.group(1), [])}

        @d("GET", V1 + r"/content/([^/]+)/child/comment")
        def comments(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            def shape(x: dict[str, Any], location: str) -> dict[str, Any]:
                return {"id": x["id"], "type": "comment", "body": x["body"], "extensions": {"location": location},
                        "version": {"when": x["version"]["createdAt"], "by": {"userKey": x["version"]["authorId"]}, "number": 1}}
            items = [shape(x, "footer") for x in c["footer_comments"].get(m.group(1), [])] + [shape(x, "inline") for x in c["inline_comments"].get(m.group(1), [])]
            return 200, paged(items, q, V1 + f"/content/{m.group(1)}/child/comment")

        @d("GET", V1 + r"/content/([^/]+)/child/attachment")
        def attachments(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            items = [{"id": x["id"], "type": "attachment", "title": x["title"], "version": x["version"], "extensions": {"mediaType": x["mediaType"], "fileSize": x["fileSize"]},
                      "_links": {"download": x["downloadLink"]}} for x in c["attachments"].get(m.group(1), [])]
            return 200, paged(items, q, V1 + f"/content/{m.group(1)}/child/attachment")

        @d("GET", r"/download/attachments/(\d+)/([^/]+)")
        def download(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            x = next((x for x in c["attachments"].get(m.group(1), []) if x["title"] == m.group(2)), None)
            return (200, c["attachment_content"][x["id"]]) if x else (404, {"message": "not found"})

        @d("GET", V1 + r"/content/([^/]+)/restriction/byOperation")
        def restrictions(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return (200, c["restrictions"].get(m.group(1), {})) if m.group(1) in pages else (404, {"message": "not found"})

        @d("GET", V1 + "/search")
        def search_cql(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            if not q.get("cql", "").strip():
                return 400, {"message": "cql must not be empty"}
            return 200, paged(c["search"], q, V1 + "/search")

        @d("GET", V1 + "/space")
        def spaces(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            keys = {q["spaceKey"]} if q.get("spaceKey") else None
            items = [s for s in c["spaces"] if (not keys or s["key"] in keys) and (not q.get("type") or s["type"] == q["type"])]
            return 200, paged(items, q, V1 + "/space")

        @d("GET", V1 + r"/space/([^/]+)")
        def space(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            s = next((x for x in c["spaces"] if x["key"] == m.group(1)), None)
            if not s:
                return 404, {"message": "No space with key : " + m.group(1)}
            return 200, {**s, "permissions": [{"operation": {"operation": "read", "targetType": "space"}, "subjects": {"group": {"results": [{"name": "eng"}]}}}]
                         if "permissions" in q.get("expand", "") else None} if "permissions" in q.get("expand", "") else s

        @d("GET", V1 + "/template/page")
        def templates(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"results": c["templates"]}

        @d("GET", V1 + r"/template/(\w+)")
        def template(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            t = next((x for x in c["templates"] if x["templateId"] == m.group(1)), None)
            return (200, t) if t else (404, {"message": "not found"})

        @d("POST", r"/rest/cb-automation/latest/hooks/[\w-]+")
        def automation(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            self.automation_calls.append({"path": m.group(0), "body": b})
            return 200, {"message": "Webhook triggered"}

        # ---- Confluence DC webhooks (/rest/api/webhooks) and property writes
        hooks: list[dict[str, Any]] = []

        @d("GET", V1 + "/webhooks")
        def hooks_list(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"results": hooks}

        @d("POST", V1 + "/webhooks")
        def hooks_create(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            hook = {"id": len(hooks) + 1, **b}
            hooks.append(hook)
            return 200, hook

        @d("POST", V1 + "/webhooks/test")
        def hooks_test(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"statusCode": 200, "url": b["url"]}

        @d("PUT", V1 + r"/webhooks/(\d+)")
        def hooks_update(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            hook = next((h for h in hooks if str(h["id"]) == m.group(1)), None)
            if hook is None:
                return 404, {"message": "no such webhook"}
            hook.update(b)
            return 200, hook

        @d("DELETE", V1 + r"/webhooks/(\d+)")
        def hooks_delete(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            hooks[:] = [h for h in hooks if str(h["id"]) != m.group(1)]
            return 204, None

        @d("GET", V1 + r"/webhooks/(\d+)/statistics")
        def hooks_stats(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            return 200, {"success": 3, "failure": 0}

        @d("POST", V1 + r"/content/([^/]+)/property")
        def prop_create(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            c["properties"].setdefault(m.group(1), []).append({"key": b["key"], "value": b["value"], "version": {"number": 1}})
            return 200, b

        @d("PUT", V1 + r"/content/([^/]+)/property/([^/]+)")
        def prop_update(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            for p in c["properties"].get(m.group(1), []):
                if p["key"] == m.group(2):
                    p.update(value=b["value"], version=b["version"])
            return 200, b

        @d("GET", V1 + r"/content/([^/]+)/property/([^/]+)")
        def prop_get(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            p = next((x for x in c["properties"].get(m.group(1), []) if x["key"] == m.group(2)), None)
            return (200, p) if p else (404, {"message": "no such property"})

        # ---- Confluence DC writes
        counter = {"n": 5000}

        def nxt() -> str:
            counter["n"] += 1
            return str(counter["n"])

        @d("POST", V1 + "/content")
        def content_create(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            if b.get("type") == "comment":
                cid = nxt()
                target = b["container"]["id"]
                c["footer_comments"].setdefault(target, []).append({"id": cid, "body": {"storage": b["body"]["storage"]}, "version": {"authorId": "u-ann", "createdAt": "2026-09-03T10:00:00.000Z"}})
                return 200, {"id": cid, "type": "comment"}
            space = next(s for s in c["spaces"] if s["key"] == b["space"]["key"])
            pid = nxt()
            parent = (b.get("ancestors") or [{}])[-1].get("id")
            pages[pid] = {"id": pid, "status": "current", "title": b["title"], "spaceId": space["id"], "authorId": "u-ann", "createdAt": "2026-09-03T10:00:00.000Z",
                          "version": {"number": 1, "createdAt": "2026-09-03T10:00:00.000Z"}, "body": {"storage": {"value": b["body"]["storage"]["value"]}},
                          "_links": {"webui": f"/spaces/{space['key']}/pages/{pid}"}}
            if parent:
                c["children"].setdefault(parent, []).append(pid)
                c["ancestors"][pid] = [*c["ancestors"].get(parent, []), parent]
            return 200, dc_page(pages[pid], "space,version,ancestors")

        @d("PUT", V1 + r"/content/([^/]+)")
        def content_update(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            p = pages.get(m.group(1))
            if not p:
                comment = next((x for v in c["footer_comments"].values() for x in v if x["id"] == m.group(1)), None)
                if comment is None:
                    return 404, {"message": "not found"}
                comment["body"] = {"storage": b["body"]["storage"]}
                return 200, {"id": comment["id"], "type": "comment"}
            if b["version"]["number"] != p["version"]["number"] + 1:
                return 409, {"message": f"Version must be incremented on update. Current version is: {p['version']['number']}"}
            p["title"], p["version"] = b["title"], {"number": b["version"]["number"], "createdAt": "2026-09-03T11:00:00.000Z", "message": b["version"].get("message")}
            p["body"] = {"storage": {"value": b["body"]["storage"]["value"]}}
            return 200, dc_page(p, "space,version")

        @d("DELETE", V1 + r"/content/([^/]+)")
        def content_delete(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            if pages.pop(m.group(1), None) is None:
                for v in c["footer_comments"].values():
                    v[:] = [x for x in v if x["id"] != m.group(1)]
                for v in c["attachments"].values():
                    v[:] = [x for x in v if x["id"] != m.group(1)]
            return 204, None

        @d("PUT", V1 + r"/content/([^/]+)/move/(\w+)/(\d+)")
        def move(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            c["ancestors"][m.group(1)] = [m.group(3)]
            return 200, {"id": m.group(1)}

        @d("POST", V1 + r"/content/([^/]+)/label")
        def label_add(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            labels = c["labels"].setdefault(m.group(1), [])
            for x in b:
                if x["name"] not in {lb["name"] for lb in labels}:
                    labels.append({"id": nxt(), "name": x["name"], "prefix": x["prefix"]})
            return 200, {"results": labels}

        @d("DELETE", V1 + r"/content/([^/]+)/label/(\w+)")
        def label_del(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            c["labels"][m.group(1)] = [x for x in c["labels"].get(m.group(1), []) if x["name"] != m.group(2)]
            return 204, None

        @d("PUT", V1 + r"/content/([^/]+)/restriction")
        def restriction_set(m: re.Match[str], q: dict[str, str], b: Any) -> tuple[int, Any]:
            out = {}
            for side in b:
                out[side["operation"]] = {"operation": side["operation"], "restrictions": {
                    "user": {"results": [{"accountId": u.get("accountId") or u.get("username"), "displayName": u.get("username") or u.get("accountId")} for u in side["restrictions"]["user"]]},
                    "group": {"results": [{"name": g["name"]} for g in side["restrictions"]["group"]]}}}
            c["restrictions"][m.group(1)] = out
            return 200, {"results": list(out.values())}
