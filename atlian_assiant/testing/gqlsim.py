"""FakeGraphQL: a stand-in for the Atlassian GraphQL gateway generated from the stored documents (design 18, layer 3).

Each stored operation is answered by replaying the equivalent REST request against a ``ScenarioServer`` and wrapping the result the way a GraphQL schema
the LIVE gateway does (shapes recorded 2026-10-08: typed Jira issue fields, ADF under ``adfValue.json``, Confluence objects by ARI, ``edges { node }``
connections, beta fields refused without their ``X-ExperimentalApi`` opt-in). Introspection answers one ``__type`` per request from the documents' own
``# requires:`` lists, so schema drift can be simulated (``missing`` / ``deprecated``).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from ..backends.graphql.documents import load_documents
from ..core.errors import AtlassianError, AuthenticationError, NotFoundError, PermissionDeniedError
from ..core.http import HttpTransport, RequestSpec
from ..core.urls import ApiSurface
from .server import ScenarioServer

J, A, S, V2, V1 = ApiSurface.JIRA_PLATFORM, ApiSurface.JIRA_AGILE, ApiSurface.JSM, ApiSurface.CONFLUENCE_V2, ApiSurface.CONFLUENCE_V1


def edges(items: list[Any]) -> dict[str, Any]:
    return {"edges": [{"node": i} for i in items]}


# REST scenario data -> the live gateway's shapes (the inverse of backends/graphql/ops.py)
def ari_id(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def agg_user(u: dict[str, Any] | None) -> dict[str, Any] | None:
    return {"accountId": u.get("accountId"), "name": u.get("displayName")} if u else None


def agg_rich(adf: Any) -> dict[str, Any] | None:
    return {"adfValue": {"json": adf}} if adf is not None else None


def agg_issue(i: dict[str, Any]) -> dict[str, Any]:
    f = i.get("fields") or {}
    st, pr, it, pj = f.get("status") or {}, f.get("priority"), f.get("issuetype") or {}, f.get("project") or {}
    cat = st.get("statusCategory") or {}
    return {"id": f"ari:cloud:jira:cloud-1:issue/{i.get('id')}", "issueId": i.get("id"), "key": i.get("key"), "webUrl": None, "summary": f.get("summary"),
            "created": f.get("created"), "updated": f.get("updated"), "resolutionDate": f.get("resolutiondate"), "dueDate": f.get("duedate"),
            "status": {"statusId": st.get("id"), "name": st.get("name"), "statusCategory": {"statusCategoryId": cat.get("id"), "key": cat.get("key"), "name": cat.get("name")}},
            "priority": {"priorityId": pr.get("id"), "name": pr.get("name")} if pr else None, "issueType": {"issueTypeId": it.get("id"), "name": it.get("name")},
            "project": {"projectId": pj.get("id"), "key": pj.get("key"), "name": pj.get("name")}, "assignee": agg_user(f.get("assignee")),
            "reporter": agg_user(f.get("reporter")), "resolution": f.get("resolution"), "description": agg_rich(f.get("description")),
            "labels": edges([{"name": lb} for lb in f.get("labels") or []])}


def agg_comment(c: dict[str, Any]) -> dict[str, Any]:
    return {"commentId": c.get("id"), "created": c.get("created"), "updated": c.get("updated"), "author": agg_user(c.get("author")), "richText": agg_rich(c.get("body"))}


def agg_worklog(w: dict[str, Any]) -> dict[str, Any]:
    return {"worklogId": w.get("id"), "created": w.get("created"), "updated": w.get("updated"), "startDate": w.get("started"), "author": agg_user(w.get("author")),
            "timeSpent": {"timeInSeconds": w.get("timeSpentSeconds")}, "workDescription": agg_rich(w.get("comment"))}


def agg_project(p: dict[str, Any]) -> dict[str, Any]:
    return {"projectId": p.get("id"), "key": p.get("key"), "name": p.get("name"), "projectType": str(p.get("projectTypeKey") or "").upper() or None}


def agg_page(p: dict[str, Any]) -> dict[str, Any]:
    ver, links = p.get("version") or {}, p.get("_links") or {}
    storage = ((p.get("body") or {}).get("storage") or {}).get("value")
    return {"pageId": p.get("id"), "title": p.get("title"), "status": str(p.get("status") or "").upper() or None, "createdAt": p.get("createdAt"),
            "space": {"spaceId": p.get("spaceId")}, "author": {"user": {"accountId": p.get("authorId")}} if p.get("authorId") else None,
            "latestVersion": {"number": ver.get("number"), "createdAt": ver.get("createdAt")} if ver else None,
            "links": {"base": links.get("base"), "webUi": links.get("webui")}, "body": {"storage": {"value": storage}} if storage is not None else None}


def agg_conf_comment(c: dict[str, Any]) -> dict[str, Any]:
    ver = c.get("version") or {}
    return {"commentId": c.get("id"), "status": str(c.get("status") or "current").upper(), "author": {"user": {"accountId": ver.get("authorId")}},
            "latestVersion": {"number": ver.get("number"), "createdAt": ver.get("createdAt")}, "body": {"storage": {"value": ((c.get("body") or {}).get("storage") or {}).get("value")}}}


class FakeGraphQL:
    ENDPOINT = "https://example.atlassian.net/gateway/api/graphql"

    def __init__(self, server: ScenarioServer, *, missing: tuple[str, ...] = (), deprecated: tuple[str, ...] = ()) -> None:
        from .. import AtlassianConfig, BackendKind, ClientFactory

        self.server, self.missing, self.deprecated = server, set(missing), set(deprecated)
        cfg = AtlassianConfig(site="example.atlassian.net", email="ann@example.com", api_token="secret-token-value")
        self._http: HttpTransport = ClientFactory().backend(BackendKind.REST, cfg, http=server.client()).transport  # type: ignore[attr-defined]
        self.calls: list[dict[str, Any]] = []
        self.responses: dict[str, Any] = {}                       # operationName -> canned response (overrides a resolver)
        self.resolvers: dict[str, Callable[[dict[str, Any]], Any]] = {}

    # ---------------------------------------------------------------------------------------------------- REST replay
    async def rest(self, method: str, surface: ApiSurface, path: str, *, params: dict[str, Any] | None = None, body: Any = None) -> Any:
        resp = await self._http.send(RequestSpec(method, surface, path, params or {}, json=body, operation="replay"))
        return resp.json() if resp.content else None

    # ---------------------------------------------------------------------------------------------------- operations (live gateway shapes)
    async def resolve(self, name: str, v: dict[str, Any]) -> dict[str, Any]:
        key = v.get("key")
        if name == "JiraIssueGet":
            return {"jira": {"issueByKey": agg_issue(await self.rest("GET", J, f"/issue/{key}"))}}
        if name in ("JiraIssueSearch", "JiraIssueCount"):
            page = await self.rest("POST", J, "/search/jql", body={"jql": v["jql"], "fields": ["*navigable"], "maxResults": 100})
            items = page["issues"]
            if name == "JiraIssueCount":
                return {"jira": {"issueSearchStable": {"totalCount": len(items)}}}
            start, size = int(v.get("after") or 0), int(v.get("first") or 50)
            chunk, more = items[start:start + size], start + size < len(items)
            return {"jira": {"issueSearchStable": {"pageInfo": {"hasNextPage": more, "endCursor": str(start + size) if more else None},
                                                   **edges([agg_issue(i) for i in chunk])}}}
        if name == "JiraIssueComments":
            return {"jira": {"issueByKey": {"comments": edges([agg_comment(c) for c in (await self.rest("GET", J, f"/issue/{key}/comment"))["comments"]])}}}
        if name == "JiraIssueWorklogs":
            return {"jira": {"issueByKey": {"worklogs": edges([agg_worklog(w) for w in (await self.rest("GET", J, f"/issue/{key}/worklog"))["worklogs"]])}}}
        if name == "JiraProjects":
            values = (await self.rest("GET", J, "/project/search", params={"query": v.get("keyword")}))["values"]
            return {"jira": {"allJiraProjects": edges([agg_project(p) for p in values])}}
        if name == "JiraProject":
            return {"jira": {"jiraProjectByKey": agg_project(await self.rest("GET", J, f"/project/{key}"))}}
        if name == "JiraMyself":
            me = await self.rest("GET", J, "/myself")
            return {"me": {"user": {"accountId": me["accountId"], "name": me.get("displayName"), "email": me.get("emailAddress")}}}
        if name in ("ConfPage", "ConfPageAncestors", "ConfPageComments", "ConfPageLabels"):
            page_id = ari_id(v["id"])
            if name == "ConfPage":
                return {"confluence": {"page": agg_page(await self.rest("GET", V2, f"/pages/{page_id}", params={"body-format": "storage"}))}}
            if name == "ConfPageAncestors":
                found = [agg_page(await self.rest("GET", V2, f"/pages/{a['id']}")) for a in (await self.rest("GET", V2, f"/pages/{page_id}/ancestors"))["results"]]
                return {"confluence": {"page": {"ancestors": found}}}
            if name == "ConfPageComments":
                kind = str(v.get("commentType") or "FOOTER").lower()
                res = await self.rest("GET", V2, f"/pages/{page_id}/{kind}-comments", params={"body-format": "storage"})
                return {"confluence": {"page": {"comments": [agg_conf_comment(c) for c in res["results"]]}}}
            labels = (await self.rest("GET", V2, f"/pages/{page_id}/labels"))["results"]
            return {"confluence": {"page": {"labels": [{"id": lb.get("id"), "label": lb.get("name"), "prefix": lb.get("prefix")} for lb in labels]}}}
        if name == "ConfSearch":
            return {"confluence_search": {"nodes": (await self.rest("GET", V1, "/search", params={"cql": v["cql"], "limit": v.get("first")}))["results"]}}
        raise KeyError(name)

    def introspection(self) -> dict[str, Any]:
        types: dict[str, dict[str, bool]] = {}
        for doc in load_documents().values():
            for req in doc.requires:
                typ, _, fld = req.partition(".")
                if req not in self.missing:
                    types.setdefault(typ, {})[fld] = req in self.deprecated
        return {"__schema": {"types": [{"name": t, "fields": [{"name": f, "isDeprecated": dep} for f, dep in fields.items()]} for t, fields in types.items()]}}

    # ---------------------------------------------------------------------------------------------------- HTTP surface
    async def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        name = body.get("operationName") or ""
        self.calls.append({"operationName": name, "variables": body.get("variables", {}), "query": body["query"], "headers": dict(request.headers)})
        if name in self.responses:
            return httpx.Response(200, json=self.responses[name])
        if name == "Introspect":
            import re as _re

            asked = _re.findall(r'__type\(name: "(\w+)"\)', body["query"])
            if len(asked) > 1:                                                   # the live gateway refuses this as bad-faith introspection
                return httpx.Response(200, json={"data": None, "errors": [{"message": "This request is not asking for introspection in good faith - "
                                                                                      "Query.__type is present too often!", "extensions": {"classification": "ValidationError"}}]})
            types = {t["name"]: t for t in self.introspection()["__schema"]["types"]}
            return httpx.Response(200, json={"data": {"__type": types.get(asked[0]) if asked else None}})
        docs = {d.name: d for d in load_documents().values()}
        missing_opt_in = [e for e in (docs[name].experimental if name in docs else ()) if e not in request.headers.get("x-experimentalapi", "").split(",")]
        if missing_opt_in:                                                       # as the live gateway: beta fields need the opt-in header
            return httpx.Response(200, json={"data": None, "errors": [{"message": f"You MUST provide a 'X-ExperimentalApi : {missing_opt_in[0]}' HTTP header",
                                                                       "extensions": {"classification": "BetaHeaderOptInException"}}]})
        if name in self.resolvers:
            return httpx.Response(200, json={"data": self.resolvers[name](body.get("variables", {}))})
        if name not in {d.name for d in load_documents().values()}:
            q = body["query"]
            if "__typename" in q:
                return httpx.Response(200, json={"data": {"__typename": "Query"}})
            if "boom" in q:
                return httpx.Response(200, json={"data": None, "errors": [{"message": "Validation error: field 'boom' is undefined", "extensions": {"classification": "ValidationError"}}]})
            return httpx.Response(200, json={"data": {"echo": body.get("variables", {})}, "errors": [{"message": "partial failure", "extensions": {"classification": "DataFetchingException"}}]
                                             if "partial" in q else None})
        try:
            return httpx.Response(200, json={"data": await self.resolve(name, body.get("variables", {}))})
        except AtlassianError as exc:
            cls = ("NOT_FOUND" if isinstance(exc, NotFoundError) else "FORBIDDEN" if isinstance(exc, PermissionDeniedError)
                   else "UNAUTHENTICATED" if isinstance(exc, AuthenticationError) else "BAD_USER_INPUT")
            return httpx.Response(200, json={"data": None, "errors": [{"message": exc.message, "extensions": {"classification": cls}}]})

    def client(self) -> httpx.AsyncClient:
        async def handler(request: httpx.Request) -> httpx.Response:
            return await self.handle(request)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))
