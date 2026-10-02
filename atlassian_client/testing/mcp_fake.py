"""FakeMcpTransport: an in-process stand-in for the Atlassian MCP v2 server, served from the shared scenario dataset.

Tool argument names and result shapes follow the assumptions documented in ``backends/mcp/backend.py`` (design section 22,
item 8). Tests can disable tools to simulate admin-disabled permission groups.
"""
from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

from ..backends.mcp.backend import OP_TOOLS
from ..backends.mcp.session import McpToolResult
from .scenario import scenario

CLOUD_ID = "cloud-1"
ALL_TOOLS = frozenset(OP_TOOLS.values())


def _off(items: list[Any], a: dict[str, Any], key: str) -> dict[str, Any]:
    start, size = int(a.get("startAt", 0)), int(a.get("maxResults", 50))
    return {"startAt": start, "maxResults": size, "total": len(items), "isLast": start + size >= len(items), key: items[start:start + size]}


def _cur(items: list[Any], a: dict[str, Any]) -> dict[str, Any]:
    start, size = int(a.get("cursor") or 0), int(a.get("maxResults", 25))
    body: dict[str, Any] = {"results": items[start:start + size]}
    if start + size < len(items):
        body["nextCursor"] = str(start + size)
    return body


class ToolError(Exception):
    pass


class FakeMcpTransport:
    def __init__(self, raw: dict[str, Any] | None = None, enabled_tools: frozenset[str] | set[str] | None = None) -> None:
        self.raw = raw or scenario()
        self.enabled = set(enabled_tools) if enabled_tools is not None else set(ALL_TOOLS)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False
        self.handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self._build()

    async def list_tools(self) -> list[str]:
        return sorted(self.enabled)

    async def aclose(self) -> None:
        self.closed = True

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpToolResult:
        self.calls.append((name, dict(arguments)))
        if name not in self.enabled or name not in self.handlers:
            return McpToolResult(text=f"Tool {name} not found", is_error=True)
        if name not in ("getAccessibleAtlassianResources", "atlassianUserInfo") and arguments.get("cloudId") != CLOUD_ID:
            return McpToolResult(text="403 Forbidden: unknown cloudId", is_error=True)
        try:
            payload = self.handlers[name](arguments)
        except ToolError as exc:
            return McpToolResult(text=str(exc), is_error=True)
        return McpToolResult(structured=payload if isinstance(payload, (dict, list)) else None, text=json.dumps(payload))

    def _build(self) -> None:
        j, a, c = self.raw["jira"], self.raw["agile"], self.raw["confluence"]
        issues = {i["key"]: i for i in j["issues"]}
        pages = c["pages"]

        def issue(args: dict[str, Any]) -> dict[str, Any]:
            i = issues.get(str(args["issueIdOrKey"]).upper())
            if not i:
                raise ToolError("404 Not Found: issue does not exist or you do not have permission")
            out: dict[str, Any] = json.loads(json.dumps(i))
            if "names" in str(args.get("expand", "")):
                out["names"] = {f["id"]: f["name"] for f in j["fields"]}
            if args.get("fields"):
                out["fields"] = {k: v for k, v in out["fields"].items() if k in args["fields"]}
            return out

        def page(args: dict[str, Any]) -> dict[str, Any]:
            p = pages.get(str(args["contentId"]))
            if not p:
                raise ToolError("404 Not Found: content does not exist")
            out: dict[str, Any] = json.loads(json.dumps(p))
            if args.get("version") and int(args["version"]) != p["version"]["number"]:
                html = c["page_versions"].get(str(args["contentId"]), {}).get(str(args["version"]))
                if html is None:
                    raise ToolError("404 Not Found: version")
                out["body"] = {"storage": {"value": html}}
                out["version"]["number"] = int(args["version"])
            fmt = args.get("bodyFormat")
            if fmt is None:
                out.pop("body", None)
            elif fmt == "view":
                out["body"] = {"view": {"value": out["body"]["storage"]["value"]}}
            out["labels"] = c["labels"].get(str(args["contentId"]), [])
            return out

        def issue_items(store: dict[str, Any], args: dict[str, Any]) -> list[Any]:
            key = str(args["issueIdOrKey"]).upper()
            if key not in issues:
                raise ToolError("404 Not Found: issue does not exist or you do not have permission")
            items: list[Any] = store.get(key, [])
            return items

        def need(store: dict[str, Any], key: str, what: str) -> Any:
            if key not in store:
                raise ToolError(f"404 Not Found: {what}")
            found: Any = store[key]
            return found

        def search_jql(args: dict[str, Any]) -> dict[str, Any]:
            start, size = int(args["nextPageToken"]) if args.get("nextPageToken") else 0, int(args.get("maxResults", 50))
            body: dict[str, Any] = {"issues": j["issues"][start:start + size]}
            if start + size < len(j["issues"]):
                body["nextPageToken"] = str(start + size)
            return body

        def children(args: dict[str, Any]) -> dict[str, Any]:
            if args.get("parentId"):
                ids = c["children"].get(str(args["parentId"]), [])
                return _cur([pages[i] for i in ids], args)
            items = [p for p in pages.values() if (not args.get("spaceId") or p["spaceId"] == args["spaceId"]) and (not args.get("title") or p["title"] == args["title"])]
            return _cur(items, args)

        def user_lookup(args: dict[str, Any]) -> list[Any]:
            t = str(args["searchString"]).lower()
            return [u for u in j["users"] if t in u["displayName"].lower() or t in (u.get("emailAddress") or "").lower()]

        def attachment_file(args: dict[str, Any]) -> dict[str, Any]:
            for i in j["issues"]:
                for x in i["fields"].get("attachment", []):
                    if x["id"] == args["attachmentId"]:
                        return {"filename": x["filename"], "mimeType": x["mimeType"], "contentBase64": base64.b64encode(j["attachment_content"][x["id"]]).decode()}
            raise ToolError("404 Not Found: attachment")

        def conf_attachment_file(args: dict[str, Any]) -> dict[str, Any]:
            for v in c["attachments"].values():
                for x in v:
                    if x["id"] == args["attachmentId"]:
                        return {"title": x["title"], "mediaType": x["mediaType"], "contentBase64": base64.b64encode(c["attachment_content"][x["id"]]).decode()}
            raise ToolError("404 Not Found: attachment")

        def diff(args: dict[str, Any]) -> dict[str, Any]:
            import difflib

            from ..formats.storage import storage_to_markdown

            versions = c["page_versions"].get(str(args["contentId"]), {})
            a_md, b_md = (storage_to_markdown(versions.get(str(args[k]), "")) for k in ("fromVersion", "toVersion"))
            return {"diff": "\n".join(difflib.unified_diff(a_md.splitlines(), b_md.splitlines(), f"v{args['fromVersion']}", f"v{args['toVersion']}", lineterm=""))}

        def tasks(args: dict[str, Any]) -> dict[str, Any]:
            items = [t for t in c["tasks"] if (not args.get("contentId") or t["pageId"] == args["contentId"]) and (not args.get("status") or t["status"] == args["status"])]
            return _cur(items, args)

        def spaces(args: dict[str, Any]) -> dict[str, Any]:
            keys = set(str(args["keys"]).split(",")) if args.get("keys") else None
            return _cur([s for s in c["spaces"] if not keys or s["key"] in keys], args)

        def space(args: dict[str, Any]) -> dict[str, Any]:
            found: dict[str, Any] | None = next((s for s in c["spaces"] if args["spaceIdOrKey"] in (s["key"], s["id"])), None)
            if not found:
                raise ToolError("404 Not Found: space")
            return found

        def comments(args: dict[str, Any]) -> dict[str, Any]:
            cid = str(args["contentId"])
            out = [{**x, "kind": "footer"} for x in c["footer_comments"].get(cid, [])] + [{**x, "kind": "inline"} for x in c["inline_comments"].get(cid, [])]
            if args.get("kind"):
                out = [x for x in out if x["kind"] == args["kind"]]
            return {"results": out}

        def search(args: dict[str, Any]) -> dict[str, Any]:
            if not str(args.get("cql", "")).strip():
                raise ToolError("400 Bad Request: cql must not be empty")
            return _cur(c["search"], args)

        def template(args: dict[str, Any]) -> dict[str, Any]:
            found: dict[str, Any] | None = next((t for t in c["templates"] if t["templateId"] == args["templateId"]), None)
            if not found:
                raise ToolError("404 Not Found: template")
            return found

        h = self.handlers
        h["getJiraIssue"] = issue
        h["searchJiraIssuesUsingJql"] = search_jql
        h["listJiraIssueComments"] = lambda a: _off(issue_items(j["comments"], a), a, "comments")
        h["listJiraIssueChangelogs"] = lambda a: _off(issue_items(j["changelog"], a), a, "values")
        h["listJiraIssueWorklogs"] = lambda a: _off(issue_items(j["worklogs"], a), a, "worklogs")
        h["listJiraIssueTransitions"] = lambda a: {"transitions": j["transitions"].get(a["issueIdOrKey"], [])}
        h["listJiraIssueRemoteIssueLinks"] = lambda a: j["remote_links"].get(a["issueIdOrKey"], [])
        h["downloadJiraIssueAttachment"] = attachment_file
        h["getJiraEntityProperty"] = lambda a: need(j["properties"].get(a["entityIdOrKey"], {}), a["propertyKey"], "property")
        h["listJiraProjects"] = lambda a: _off([p for p in j["projects"] if str(a.get("query") or "").lower() in p["name"].lower() or str(a.get("query") or "").lower() in p["key"].lower()], a, "values")
        h["listJiraProjectComponents"] = lambda a: j["components"].get(a["projectIdOrKey"], [])
        h["getJiraProjectVersions"] = lambda a: j["versions"].get(a["projectIdOrKey"], [])
        h["listJiraProjectIssueTypesMetadata"] = lambda a: {"issueTypes": j["issue_types"]}
        h["getJiraIssueTypeMetaWithFields"] = lambda a: {"fields": j["create_fields"].get(f"{a['projectIdOrKey']}/{a['issueTypeId']}", [])}
        h["listJiraStatuses"] = lambda a: j["statuses"]
        h["listJiraIssueLinkTypes"] = lambda a: {"issueLinkTypes": j["link_types"]}
        h["listJiraFilters"] = lambda a: _off([f for f in j["filters"] if str(a.get("filterName") or "").lower() in f["name"].lower()], a, "values")
        h["listJiraDashboards"] = lambda a: _off(j["dashboards"], a, "values")
        h["getJiraCurrentUser"] = lambda a: j["myself"]
        h["getJiraUser"] = lambda a: need({u["accountId"]: u for u in j["users"]}, a["accountId"], "user")
        h["lookupJiraAccountId"] = user_lookup
        h["listJiraIssueAssignableUsers"] = lambda a: j["users"]
        h["listJiraBoards"] = lambda x: _off(a["boards"], x, "values")
        h["getJiraBoardConfig"] = lambda x: need(a["configs"], str(x["boardId"]), "board")
        h["getJiraBoardIssueData"] = lambda x: _off(need(a["board_issues"], str(x["boardId"]), "board"), x, "issues")
        h["listJiraBoardSprints"] = lambda x: _off([s for s in a["sprints"].get(str(x["boardId"]), []) if s["state"] in str(x.get("state", "active,future")).split(",")], x, "values")
        h["getJiraBoardSprintData"] = lambda x: _off(need(a["sprint_issues"], str(x["sprintId"]), "sprint"), x, "issues")
        h["getConfluenceContent"] = page
        h["listConfluenceContent"] = children
        h["listConfluenceContentVersions"] = lambda x: _cur(c["versions"].get(str(x["contentId"]), []), x)
        h["diffConfluenceContentVersions"] = diff
        h["resolveConfluenceContentMacros"] = lambda x: {"html": need(pages, str(x["contentId"]), "content")["body"]["storage"]["value"], "id": str(x["contentId"])}
        h["listConfluenceComments"] = comments
        h["listConfluenceAttachments"] = lambda x: _cur(c["attachments"].get(str(x["contentId"]), []), x)
        h["downloadConfluenceAttachment"] = conf_attachment_file
        h["getConfluenceContentPermissions"] = lambda x: {"operations": c["operations"].get(str(x["contentId"]), [])}
        h["getConfluenceContentRestrictionState"] = lambda x: need(c["restrictions"], str(x["contentId"]), "content")
        h["listConfluenceTasks"] = tasks
        h["listConfluenceTemplates"] = lambda x: {"results": [t for t in c["templates"] if not x.get("spaceKey") or t["space"]["key"] == x["spaceKey"]]}
        h["getConfluenceTemplate"] = template
        h["getConfluenceReactions"] = lambda x: []
        h["listConfluenceSpaces"] = spaces
        h["getConfluenceSpace"] = space
        h["getConfluencePersonalSpace"] = lambda x: need({}, "personal", "personal space")
        h["getConfluenceSpaceInstructions"] = lambda x: {"instructions": "Be concise."}
        h["searchConfluence"] = search
        h["getTeamworkGraphContext"] = lambda x: {"related": [{"ari": "ari:cloud:jira::issue/10001", "type": "issue", "title": "Login fails", "url": "https://example.atlassian.net/browse/PROJ-1"}][: int(x.get("maxItems", 50))],
                                                  "relationships": [{"type": "mentions"}]}
        h["getTeamworkGraphObject"] = lambda x: {"objects": [{"ari": u, "title": u} for u in x["ariOrUrls"]]}
        h["search"] = lambda x: {"results": [{"title": "Runbook", "url": "https://example.atlassian.net/wiki/spaces/ENG/pages/123", "product": "confluence", "snippet": x["query"], "score": 0.9}][: int(x.get("limit", 20))]}
        h["atlassianUserInfo"] = lambda x: {"account_id": j["myself"]["accountId"], "name": j["myself"]["displayName"], "email": j["myself"]["emailAddress"]}
        h["getAccessibleAtlassianResources"] = lambda x: [{"id": CLOUD_ID, "name": "Example", "url": "https://example.atlassian.net", "scopes": ["read:jira:agent-interface"]}]
