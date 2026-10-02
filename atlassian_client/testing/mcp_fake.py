"""FakeMcpTransport: an in-process stand-in for the Atlassian MCP v2 server, served from the shared scenario dataset.

Tool argument names and result shapes follow the assumptions documented in ``backends/mcp/backend.py`` (design section 22,
item 8). Tests can disable tools to simulate admin-disabled permission groups.
"""
from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

from ..backends.mcp.backend import ALL_OP_TOOLS
from ..backends.mcp.session import McpToolResult
from .scenario import scenario

CLOUD_ID = "cloud-1"
from ..backends.mcp.write import PAIRED_TOOLS  # noqa: E402

ALL_TOOLS = frozenset(ALL_OP_TOOLS.values()) | PAIRED_TOOLS
WRITE_TOOLS = ALL_TOOLS - frozenset(t for o, t in __import__('atlassian_client.backends.mcp.backend', fromlist=['OP_TOOLS']).OP_TOOLS.items())


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
        pages = c["pages"]

        def issue(args: dict[str, Any]) -> dict[str, Any]:
            i = next((x for x in j["issues"] if x["key"] == str(args["issueIdOrKey"]).upper()), None)
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
            if not any(x["key"] == key for x in j["issues"]):
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
        self._build_writes()

    def _build_writes(self) -> None:
        j, a, c = self.raw["jira"], self.raw["agile"], self.raw["confluence"]
        h = self.handlers
        n = {"v": 7000}
        me = j["myself"]

        def nxt() -> int:
            n["v"] += 1
            return n["v"]

        def find(key: str) -> dict[str, Any]:
            found: dict[str, Any] | None = next((i for i in j["issues"] if i["key"] == str(key).upper()), None)
            if found is None:
                raise ToolError("404 Not Found: issue does not exist or you do not have permission")
            return found

        def create_issue(x: dict[str, Any]) -> dict[str, Any]:
            if not str(x.get("summary", "")).strip():
                raise ToolError("400 Bad Request: summary is required")
            proj = next((p for p in j["projects"] if p["key"] == x["projectKey"]), {"id": "9", "key": x["projectKey"], "name": ""})
            num = nxt()
            user = next((u for u in j["users"] if u["accountId"] == x.get("assignee_account_id")), None)
            issue: dict[str, Any] = {"id": str(10000 + num), "key": f"{proj['key']}-{num}", "fields": {
                "summary": x["summary"], "status": {"id": "1", "name": "To Do", "statusCategory": {"name": "To Do"}}, "issuetype": {"name": x["issueTypeName"]},
                "project": {"id": proj["id"], "key": proj["key"], "name": proj.get("name")}, "assignee": user, "reporter": me, "labels": list(x.get("labels") or []),
                "description": None, "attachment": [], "issuelinks": [], "created": "2026-10-01T10:00:00.000+0000", "updated": "2026-10-01T10:00:00.000+0000"}}
            if x.get("description"):
                from ..formats.adf import markdown_to_adf

                issue["fields"]["description"] = markdown_to_adf(x["description"])
            j["issues"].append(issue)
            return {"id": issue["id"], "key": issue["key"]}

        def edit_issue(x: dict[str, Any]) -> dict[str, Any]:
            issue = find(x["issueIdOrKey"])
            fields = dict(x["fields"])
            if "description" in fields and isinstance(fields["description"], str):
                from ..formats.adf import markdown_to_adf

                fields["description"] = markdown_to_adf(fields["description"])
            if "assignee" in fields and fields["assignee"]:
                fields["assignee"] = next((u for u in j["users"] if u["accountId"] == fields["assignee"]["accountId"]), None)
            issue["fields"].update(fields)
            return {"ok": True}

        def transition(x: dict[str, Any]) -> dict[str, Any]:
            issue = find(x["issueIdOrKey"])
            t = next((t for t in j["transitions"].get(issue["key"], []) if t["id"] == x["transition"]["id"]), None)
            if not t:
                raise ToolError("400 Bad Request: invalid transition")
            issue["fields"]["status"] = t["to"]
            if x.get("commentBody"):
                from ..formats.adf import markdown_to_adf

                j["comments"].setdefault(issue["key"], []).append({"id": f"c{nxt()}", "author": me, "body": markdown_to_adf(x["commentBody"]), "created": "2026-10-01T10:00:00.000+0000"})
            return {"ok": True}

        def comment(x: dict[str, Any]) -> dict[str, Any]:
            from ..formats.adf import markdown_to_adf

            issue = find(x["issueIdOrKey"])
            items = j["comments"].setdefault(issue["key"], [])
            if x.get("commentId"):
                existing = next((c0 for c0 in items if c0["id"] == x["commentId"]), None)
                if not existing:
                    raise ToolError("404 Not Found: comment")
                existing["body"] = markdown_to_adf(x["commentBody"])
                return existing  # type: ignore[no-any-return]
            cm = {"id": f"c{nxt()}", "author": me, "body": markdown_to_adf(x["commentBody"]), "created": "2026-10-01T10:00:00.000+0000", "updated": "2026-10-01T10:00:00.000+0000"}
            items.append(cm)
            return cm

        def worklog(x: dict[str, Any]) -> dict[str, Any]:
            issue = find(x["issueIdOrKey"])
            w = {"id": f"w{nxt()}", "author": me, "timeSpent": x["timeSpent"], "timeSpentSeconds": 3600, "started": x.get("started") or "2026-10-01T09:00:00.000+0000"}
            j["worklogs"].setdefault(issue["key"], []).append(w)
            return w

        def link(x: dict[str, Any]) -> dict[str, Any]:
            inward, outward = find(x["inwardIssue"]), find(x["outwardIssue"])
            inward["fields"]["issuelinks"].append({"id": f"l{nxt()}", "type": {"name": x["type"], "outward": x["type"].lower()}, "outwardIssue": {"key": outward["key"], "fields": {}}})
            return {"ok": True}

        def remote(x: dict[str, Any]) -> dict[str, Any]:
            issue = find(x["issueIdOrKey"])
            rl = {"id": nxt(), "object": {"url": x["url"], "title": x["title"]}}
            j["remote_links"].setdefault(issue["key"], []).append(rl)
            return rl

        def upload(x: dict[str, Any]) -> list[dict[str, Any]]:
            issue = find(x["issueIdOrKey"])
            data = base64.b64decode(x["contentBase64"])
            att = {"id": f"att{nxt()}", "filename": x["filename"], "size": len(data), "mimeType": "text/plain", "author": me}
            issue["fields"]["attachment"].append(att)
            j["attachment_content"][att["id"]] = data
            return [att]

        def watch(x: dict[str, Any]) -> dict[str, Any]:
            issue = find(x["issueIdOrKey"])
            w = j["watchers"].setdefault(issue["key"], [])
            user = next((u for u in j["users"] if u["accountId"] == (x.get("accountId") or me["accountId"])), me)
            if x["watch"] and user not in w:
                w.append(user)
            if not x["watch"]:
                j["watchers"][issue["key"]] = [u for u in w if u["accountId"] != user["accountId"]]
            return {"ok": True}

        def prop(x: dict[str, Any]) -> dict[str, Any]:
            find(x["entityIdOrKey"])
            j["properties"].setdefault(str(x["entityIdOrKey"]).upper(), {})[x["propertyKey"]] = {"key": x["propertyKey"], "value": x["value"]}
            return {"ok": True}

        def delete_issue(x: dict[str, Any]) -> dict[str, Any]:
            find(x["issueIdOrKey"])
            j["issues"] = [i for i in j["issues"] if i["key"] != str(x["issueIdOrKey"]).upper()]
            return {"ok": True}

        def delete_comment(x: dict[str, Any]) -> dict[str, Any]:
            items = j["comments"].get(str(x["issueIdOrKey"]).upper(), [])
            if not any(c0["id"] == x["commentId"] for c0 in items):
                raise ToolError("404 Not Found: comment")
            j["comments"][str(x["issueIdOrKey"]).upper()] = [c0 for c0 in items if c0["id"] != x["commentId"]]
            return {"ok": True}

        def delete_attachment(x: dict[str, Any]) -> dict[str, Any]:
            if x["attachmentId"] not in j["attachment_content"]:
                raise ToolError("404 Not Found: attachment")
            del j["attachment_content"][x["attachmentId"]]
            for i in j["issues"]:
                i["fields"]["attachment"] = [at for at in i["fields"].get("attachment", []) if at["id"] != x["attachmentId"]]
            return {"ok": True}

        def version(x: dict[str, Any]) -> dict[str, Any]:
            versions = j["versions"].setdefault(x["projectIdOrKey"], [])
            if x["action"] == "create":
                v = {"id": f"v{nxt()}", "name": x["name"], "released": False}
                versions.append(v)
                return v
            v2: dict[str, Any] | None = next((v for v in versions if v["id"] == x.get("versionId")), None)
            if not v2:
                raise ToolError("404 Not Found: version")
            if x["action"] == "delete":
                versions.remove(v2)
            elif x["action"] == "release":
                v2["released"] = True
            return v2

        def create_project(x: dict[str, Any]) -> dict[str, Any]:
            p = {"id": str(nxt()), "key": x["key"], "name": x["name"], "projectTypeKey": "software"}
            j["projects"].append(p)
            return p

        def update_project(x: dict[str, Any]) -> dict[str, Any]:
            p = next((p for p in j["projects"] if p["key"] == x["projectIdOrKey"]), None)
            if not p:
                raise ToolError("404 Not Found: project")
            p.update({k: v for k, v in x.items() if k not in ("cloudId", "projectIdOrKey")})
            return p  # type: ignore[no-any-return]

        def sprint(x: dict[str, Any]) -> dict[str, Any]:
            act = x["action"]
            if act == "create":
                sp = {"id": nxt(), "name": x["name"], "state": "future", "goal": x.get("goal")}
                a["sprints"].setdefault(str(x["boardId"]), []).append(sp)
                return sp
            if act in ("move_issues", "move_to_backlog", "rank"):
                if act == "move_issues":
                    a["sprint_issues"].setdefault(str(x["sprintId"]), []).extend(find(k) for k in x["issues"])
                return {"ok": True}
            for v in a["sprints"].values():
                for sp in v:
                    if sp["id"] == x["sprintId"]:
                        if act == "delete":
                            v.remove(sp)
                            return {**sp, "state": "deleted"}
                        sp["state"] = {"start": "active", "close": "closed"}.get(act, sp["state"])
                        return sp  # type: ignore[no-any-return]
            raise ToolError("404 Not Found: sprint")

        def board(x: dict[str, Any]) -> dict[str, Any]:
            b = {"id": nxt(), "name": x["name"], "type": x["type"]}
            a["boards"].append(b)
            return b

        h["createJiraIssue"] = create_issue
        h["editJiraIssue"] = edit_issue
        h["transitionJiraIssue"] = transition
        h["addOrEditJiraIssueComment"] = comment
        h["addOrEditJiraIssueWorklog"] = worklog
        h["createJiraIssueLink"] = link
        h["createJiraIssueRemoteIssueLink"] = remote
        h["uploadAttachmentToJiraIssue"] = upload
        h["watchJiraIssue"] = watch
        h["editJiraEntityProperty"] = prop
        h["convertJiraIssueHierarchy"] = lambda x: (find(x["issueIdOrKey"])["fields"].update({"issuetype": {"name": x["newIssueType"]}}) or {"ok": True})
        h["deleteJiraIssue"] = delete_issue
        h["deleteJiraComment"] = delete_comment
        h["deleteJiraIssueAttachment"] = delete_attachment
        h["manageJiraProjectVersion"] = version
        h["createJiraProject"] = create_project
        h["updateJiraProject"] = update_project
        h["getJiraWorkflows"] = lambda x: {"workflows": [{"id": "wf1", "name": "Software workflow", "statuses": ["To Do", "Done"]}]}
        h["getJiraWorkflowEditorLink"] = lambda x: {"url": f"https://example.atlassian.net/jira/settings/issues/workflows/{x['workflowId']}"}
        h["getJiraScreen"] = lambda x: {"id": x["screenId"], "name": "Default screen"}
        h["updateJiraScreen"] = lambda x: {"id": x["screenId"], "name": x.get("name", "Default screen")}
        h["manageJiraSprint"] = sprint
        h["createJiraBoard"] = board

        # ---- Confluence ----
        pages = c["pages"]

        def page(cid: str) -> dict[str, Any]:
            found: dict[str, Any] | None = pages.get(str(cid))
            if found is None:
                raise ToolError("404 Not Found: content does not exist")
            return found

        def create_content(x: dict[str, Any]) -> dict[str, Any]:
            sp = next((s for s in c["spaces"] if x["spaceKey"] in (s["key"], s["id"])), None)
            if not sp:
                raise ToolError("404 Not Found: space")
            from ..formats.storage import markdown_to_storage

            cid = str(nxt())
            p = {"id": cid, "status": x.get("status", "current"), "title": x["title"], "spaceId": sp["id"], "parentId": x.get("parentId"), "authorId": "acc-ann",
                 "createdAt": "2026-10-01T10:00:00.000Z", "version": {"number": 1, "createdAt": "2026-10-01T10:00:00.000Z"},
                 "body": {"storage": {"value": markdown_to_storage(x.get("body") or "")}}, "_links": {"webui": f"/spaces/ENG/pages/{cid}"}}
            pages[cid] = p
            return p

        def update_content(x: dict[str, Any]) -> dict[str, Any]:
            from ..formats.storage import markdown_to_storage

            p = page(x["contentId"])
            if x.get("version") is not None and int(x["version"]) != p["version"]["number"]:
                raise ToolError("409 Conflict: version mismatch")
            if x.get("title"):
                p["title"] = x["title"]
            if x.get("body") is not None:
                c["page_versions"].setdefault(p["id"], {})[str(p["version"]["number"])] = p["body"]["storage"]["value"]
                p["body"] = {"storage": {"value": markdown_to_storage(x["body"])}}
            p["version"] = {"number": p["version"]["number"] + 1, "createdAt": "2026-10-02T10:00:00.000Z", "message": x.get("versionMessage")}
            return p

        def move(x: dict[str, Any]) -> dict[str, Any]:
            p = page(x["contentId"])
            p["parentId"] = x.get("targetParentId")
            return p

        def copy(x: dict[str, Any]) -> dict[str, Any]:
            src = page(x["contentId"])
            cid = str(nxt())
            pages[cid] = {**json.loads(json.dumps(src)), "id": cid, "parentId": x["destinationParentId"], "title": x.get("title") or f"Copy of {src['title']}",
                          "version": {"number": 1, "createdAt": "2026-10-01T10:00:00.000Z"}}
            return pages[cid]  # type: ignore[no-any-return]

        def archive(x: dict[str, Any]) -> dict[str, Any]:
            for cid in x["contentIds"]:
                page(cid)["status"] = "archived"
            return {"ok": True}

        def unarchive(x: dict[str, Any]) -> dict[str, Any]:
            for cid in x["contentIds"]:
                page(cid)["status"] = "current"
            return {"ok": True}

        def restore(x: dict[str, Any]) -> dict[str, Any]:
            p = page(x["contentId"])
            html = c["page_versions"].get(p["id"], {}).get(str(x["version"]))
            if html is None:
                raise ToolError("404 Not Found: version")
            p["body"] = {"storage": {"value": html}}
            p["version"] = {"number": p["version"]["number"] + 1, "createdAt": "2026-10-02T10:00:00.000Z"}
            return p

        def labels(x: dict[str, Any]) -> dict[str, Any]:
            page(x["contentId"])
            cur = c["labels"].setdefault(str(x["contentId"]), [])
            for name in x["labels"]:
                if name not in {lb["name"] for lb in cur}:
                    cur.append({"id": str(nxt()), "name": name, "prefix": "global"})
            return {"labels": cur}

        def add_comment(x: dict[str, Any]) -> dict[str, Any]:
            page(x["contentId"])
            kind = "inline" if x.get("inlineAnchor") else "footer"
            from ..formats.storage import markdown_to_storage

            cm = {"id": f"{'ic' if kind == 'inline' else 'fc'}{nxt()}", "body": {"storage": {"value": markdown_to_storage(x["body"])}},
                  "version": {"number": 1, "authorId": "acc-ann", "createdAt": "2026-10-01T10:00:00.000Z"}, **({"resolutionStatus": "open"} if kind == "inline" else {})}
            c["inline_comments" if kind == "inline" else "footer_comments"].setdefault(str(x["contentId"]), []).append(cm)
            return {**cm, "kind": kind}

        def find_comment(cid: str) -> dict[str, Any]:
            for store in (c["footer_comments"], c["inline_comments"]):
                for items in store.values():
                    for cm in items:
                        if cm["id"] == cid:
                            return cm  # type: ignore[no-any-return]
            raise ToolError("404 Not Found: comment")

        def update_comment(x: dict[str, Any]) -> dict[str, Any]:
            from ..formats.storage import markdown_to_storage

            cm = find_comment(x["commentId"])
            cm["body"] = {"storage": {"value": markdown_to_storage(x["body"])}}
            return cm

        def resolve(x: dict[str, Any]) -> dict[str, Any]:
            find_comment(x["commentId"])["resolutionStatus"] = "resolved" if x["resolved"] else "open"
            return {"ok": True}

        def conf_upload(x: dict[str, Any]) -> dict[str, Any]:
            page(x["contentId"])
            data = base64.b64decode(x["contentBase64"])
            att = {"id": f"cat{nxt()}", "title": x["filename"], "mediaType": "text/plain", "fileSize": len(data), "pageId": str(x["contentId"]),
                   "downloadLink": f"/download/attachments/{x['contentId']}/{x['filename']}", "version": {"number": 1}}
            c["attachments"].setdefault(str(x["contentId"]), []).append(att)
            c["attachment_content"][att["id"]] = data
            return att

        def restriction(x: dict[str, Any]) -> dict[str, Any]:
            page(x["contentId"])
            c["restrictions"][str(x["contentId"])] = {
                "read": {"operation": "read", "restrictions": {"user": {"results": [{"accountId": u, "displayName": u} for u in x.get("readUsers", [])]},
                                                                "group": {"results": [{"name": g} for g in x.get("readGroups", [])]}}},
                "update": {"operation": "update", "restrictions": {"user": {"results": [{"accountId": u, "displayName": u} for u in x.get("updateUsers", [])]},
                                                                    "group": {"results": [{"name": g} for g in x.get("updateGroups", [])]}}}}
            return {"ok": True}

        def task(status: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
            def go(x: dict[str, Any]) -> dict[str, Any]:
                t = next((t for t in c["tasks"] if t["id"] == x["taskId"]), None)
                if not t:
                    raise ToolError("404 Not Found: task")
                t["status"] = status
                return t  # type: ignore[no-any-return]
            return go

        def create_space(x: dict[str, Any]) -> dict[str, Any]:
            sp = {"id": str(nxt()), "key": x["key"], "name": x["name"], "type": "global", "status": "current"}
            c["spaces"].append(sp)
            return sp

        ack: Callable[[dict[str, Any]], dict[str, Any]] = lambda x: {"ok": True}  # noqa: E731
        h["createConfluenceContent"] = create_content
        h["updateConfluenceContent"] = update_content
        h["moveConfluenceContent"] = move
        h["copyConfluenceContent"] = copy
        h["archiveConfluenceContent"] = archive
        h["unarchiveConfluenceContent"] = unarchive
        h["restoreConfluenceContentVersion"] = restore
        h["setConfluenceContentStatus"] = lambda x: (page(x["contentId"]).update({"state": x["status"]}) or {"ok": True})
        h["addLabelsToConfluenceContent"] = labels
        h["createConfluenceComment"] = add_comment
        h["updateConfluenceComment"] = update_comment
        h["updateConfluenceCommentResolution"] = resolve
        h["createConfluenceAttachment"] = conf_upload
        h["setConfluenceContentRestrictionState"] = restriction
        for tool in ("addConfluenceContentPermissions", "removeConfluenceContentPermissions", "replaceConfluenceContentPermissions", "enableConfluencePublicLink",
                     "disableConfluencePublicLink", "starConfluenceContent", "unstarConfluenceContent", "starConfluenceSpace", "unstarConfluenceSpace",
                     "watchConfluenceContent", "unwatchConfluenceContent", "watchConfluenceSpace", "unwatchConfluenceSpace", "watchConfluenceLabel",
                     "unwatchConfluenceLabel", "addConfluenceReaction", "setConfluenceSpaceInstructions", "addTeamworkGraphContext"):
            h[tool] = ack
        h["completeConfluenceTask"] = task("complete")
        h["reopenConfluenceTask"] = task("incomplete")
        h["createConfluenceSpace"] = create_space
        h["createConfluenceInfographicForPage"] = lambda x: {"id": f"inf{nxt()}", "contentId": x["contentId"], "prompt": x["prompt"]}
        h["editConfluenceInfographicForPage"] = lambda x: {"id": x["infographicId"], "prompt": x["prompt"]}
        h["createConfluenceMauiApp"] = lambda x: {"id": f"app{nxt()}", "contentId": x["contentId"], "spec": x["spec"]}
        h["editConfluenceMauiApp"] = lambda x: {"id": x["appId"], "spec": x["spec"]}
        h["getConfluenceMauiApp"] = lambda x: {"id": x["appId"], "spec": "demo"}
