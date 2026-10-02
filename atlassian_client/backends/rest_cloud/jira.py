"""Jira Platform REST v3 reads: issues, search, projects, metadata, users, JQL tools (design sections 3.1, 9.2)."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ...core.errors import AmbiguousReferenceError, InvalidRequestError, NotFoundError
from ...core.http import RequestSpec
from ...core.pagination import NextPageTokenPagination, OffsetPagination
from ...core.urls import ApiSurface
from ...models.common import Page, UserRef
from ...models.jira import (
    Attachment,
    AttachmentContent,
    ChangelogEntry,
    Comment,
    Component,
    Dashboard,
    Field,
    Filter,
    Issue,
    IssueLink,
    IssueType,
    JqlParseResult,
    Project,
    RemoteLink,
    ServerInfo,
    Status,
    Transition,
    Version,
    Worklog,
)
from . import normalize as n
from .base import RestBase, csv

J = ApiSurface.JIRA_PLATFORM


class JiraRest(RestBase):
    """Implements JiraIssueReader, JiraIssueSearcher, JiraProjectReader, JiraMetadataReader, JiraUserDirectory, JiraJqlTools."""

    # ---------------------------------------------------------------- issue reader
    async def get_issue(self, key: str, *, fields: Sequence[str] | None = None, expand: Sequence[str] = ()) -> Issue:
        exp = list(dict.fromkeys([*expand, "names"]))
        data = await self._json(J, f"/issue/{key}", params={"fields": csv(fields), "expand": csv(exp)}, operation="jira.issue.get")
        return n.issue(data, self.site_url)

    async def list_comments(self, key: str, *, limit: int = 50, cursor: str | None = None) -> Page[Comment]:
        spec = RequestSpec("GET", J, f"/issue/{key}/comment", operation="jira.issue.comments.list")
        return await self._page(spec, OffsetPagination("comments"), n.comment, limit=limit, cursor=cursor)

    async def list_changelog(self, key: str, *, limit: int = 100, cursor: str | None = None) -> Page[ChangelogEntry]:
        spec = RequestSpec("GET", J, f"/issue/{key}/changelog", operation="jira.issue.changelog.list")
        return await self._page(spec, OffsetPagination("values"), n.changelog, limit=limit, cursor=cursor)

    async def list_worklogs(self, key: str, *, limit: int = 100, cursor: str | None = None) -> Page[Worklog]:
        spec = RequestSpec("GET", J, f"/issue/{key}/worklog", operation="jira.issue.worklogs.list")
        return await self._page(spec, OffsetPagination("worklogs"), n.worklog, limit=limit, cursor=cursor)

    async def list_transitions(self, key: str) -> list[Transition]:
        data = await self._json(J, f"/issue/{key}/transitions", operation="jira.issue.transitions.list")
        return [n.transition(t) for t in data.get("transitions", [])]

    async def list_attachments(self, key: str) -> list[Attachment]:
        data = await self._json(J, f"/issue/{key}", params={"fields": "attachment"}, operation="jira.issue.attachments.list")
        return [n.attachment(a) for a in (data.get("fields") or {}).get("attachment", [])]

    async def download_attachment(self, attachment_id: str, *, dest: Path | None = None, max_bytes: int | None = None,
                                  extract_text: bool = False) -> AttachmentContent:
        meta = await self._json(J, f"/attachment/{attachment_id}", operation="jira.issue.attachment.download")
        size = int(meta.get("size") or 0)
        if max_bytes is not None and size > max_bytes:
            from ...core.errors import error_for_status

            raise error_for_status(413, f"attachment is {size} bytes, above max_bytes={max_bytes}", operation="jira.issue.attachment.download")
        data = await self.transport.stream_download(
            RequestSpec("GET", J, f"/attachment/content/{attachment_id}", headers={"Accept": "*/*"},
                        operation="jira.issue.attachment.download"), max_bytes=max_bytes)
        mime = meta.get("mimeType")
        text = data.decode("utf-8", errors="replace") if extract_text and (mime or "").startswith("text/") else None
        path = None
        if dest is not None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            path = str(dest)
        return AttachmentContent(attachment_id=str(attachment_id), filename=meta.get("filename"), mime_type=mime,
                                 size=len(data), data=None if dest else data, path=path, text=text)

    async def list_links(self, key: str) -> list[IssueLink]:
        data = await self._json(J, f"/issue/{key}", params={"fields": "issuelinks"}, operation="jira.issue.links.list")
        return [n.issue_link(link) for link in (data.get("fields") or {}).get("issuelinks", [])]

    async def list_remote_links(self, key: str) -> list[RemoteLink]:
        data = await self._json(J, f"/issue/{key}/remotelink", operation="jira.issue.remote_links.list")
        return [n.remote_link(r) for r in data or []]

    async def list_watchers(self, key: str) -> list[UserRef]:
        data = await self._json(J, f"/issue/{key}/watchers", operation="jira.issue.watchers.list")
        return [u for u in (n.user(w) for w in data.get("watchers", [])) if u]

    async def get_property(self, key: str, property_key: str) -> Any:
        data = await self._json(J, f"/issue/{key}/properties/{property_key}", operation="jira.issue.property.get")
        return data.get("value")

    # ---------------------------------------------------------------- searcher
    async def search_issues(self, jql: str, *, fields: Sequence[str] | None = None, limit: int = 50, cursor: str | None = None,
                            reconcile_issue_ids: Sequence[int] = ()) -> Page[Issue]:
        """POST /rest/api/3/search/jql (enhanced search). The legacy /search endpoint is being removed and is never used."""
        body: dict[str, Any] = {"jql": jql, "fields": list(fields) if fields else ["*navigable"]}
        if reconcile_issue_ids:
            body["reconcileIssues"] = list(reconcile_issue_ids)
        spec = RequestSpec("POST", J, "/search/jql", json=body, operation="jira.issue.search")
        return await self._page(spec, NextPageTokenPagination(), lambda d: n.issue(d, self.site_url), limit=limit, cursor=cursor)

    async def count_issues(self, jql: str) -> int:
        try:
            data = await self._json(J, "/search/approximate-count", method="POST", json={"jql": jql}, operation="jira.issue.count")
            return int(data["count"])
        except NotFoundError:
            pass                                   # endpoint unavailable on this tenant: page ids instead (design 22 item 11)
        total, cursor = 0, None
        while True:
            page = await self.search_issues(jql, fields=["key"], limit=100, cursor=cursor)
            total += len(page.items)
            if not page.next_cursor:
                return total
            cursor = page.next_cursor

    # ---------------------------------------------------------------- projects
    async def list_projects(self, *, query: str | None = None, limit: int = 50, cursor: str | None = None) -> Page[Project]:
        spec = RequestSpec("GET", J, "/project/search", params={"query": query}, operation="jira.project.list")
        return await self._page(spec, OffsetPagination("values"), n.project, limit=limit, cursor=cursor)

    async def get_project(self, key: str) -> Project:
        return n.project(await self._json(J, f"/project/{key}", operation="jira.project.get"))

    async def list_components(self, project: str) -> list[Component]:
        data = await self._json(J, f"/project/{project}/components", operation="jira.project.components.list")
        return [n.component(c) for c in data]

    async def list_versions(self, project: str) -> list[Version]:
        data = await self._json(J, f"/project/{project}/versions", operation="jira.project.versions.list")
        return [n.version(v) for v in data]

    # ---------------------------------------------------------------- metadata
    async def list_fields(self) -> list[Field]:
        return [n.jfield(f) for f in await self._json(J, "/field", operation="jira.meta.fields.list")]

    async def list_issue_types(self, project: str | None = None) -> list[IssueType]:
        if project:
            data = await self._json(J, f"/project/{project}", operation="jira.meta.issue_types.list")
            return [n.issue_type(t) for t in data.get("issueTypes", [])]
        return [n.issue_type(t) for t in await self._json(J, "/issuetype", operation="jira.meta.issue_types.list")]

    async def get_create_fields(self, project: str, issue_type: str) -> list[Field]:
        types = await self.list_issue_types(project)
        match = [t for t in types if issue_type in (t.id, t.name) or t.name.lower() == issue_type.lower()]
        if not match:
            raise NotFoundError(f"issue type {issue_type!r} not found in project {project}",
                                hint="Valid types: " + ", ".join(t.name for t in types))
        data = await self._json(J, f"/issue/createmeta/{project}/issuetypes/{match[0].id}", params={"maxResults": 200},
                                operation="jira.meta.create_fields")
        out = []
        for f in data.get("fields") or data.get("values") or []:
            fld = n.jfield({"id": f.get("fieldId") or f.get("key"), "name": f.get("name"), "schema": f.get("schema"),
                            "custom": str(f.get("fieldId", "")).startswith("customfield_"), "required": f.get("required")})
            out.append(fld)
        return out

    async def list_statuses(self, project: str | None = None) -> list[Status]:
        if project:
            data = await self._json(J, f"/project/{project}/statuses", operation="jira.meta.statuses.list")
            seen: dict[str, Status] = {}
            for it in data:
                for s in it.get("statuses", []):
                    st = n.status(s)
                    if st:
                        seen[st.id] = st
            return list(seen.values())
        return [s for s in (n.status(x) for x in await self._json(J, "/status", operation="jira.meta.statuses.list")) if s]

    async def list_priorities(self) -> list[str]:
        return [p["name"] for p in await self._json(J, "/priority", operation="jira.meta.priorities.list")]

    async def list_link_types(self) -> list[str]:
        data = await self._json(J, "/issueLinkType", operation="jira.meta.link_types.list")
        return [t["name"] for t in data.get("issueLinkTypes", [])]

    async def list_filters(self, *, query: str | None = None, limit: int = 50, cursor: str | None = None) -> Page[Filter]:
        spec = RequestSpec("GET", J, "/filter/search", params={"filterName": query, "expand": "jql"}, operation="jira.filter.list")
        return await self._page(spec, OffsetPagination("values"), n.jfilter, limit=limit, cursor=cursor)

    async def list_dashboards(self, *, limit: int = 50, cursor: str | None = None) -> Page[Dashboard]:
        spec = RequestSpec("GET", J, "/dashboard/search", operation="jira.dashboard.list")
        return await self._page(spec, OffsetPagination("values"), n.dashboard, limit=limit, cursor=cursor)

    async def server_info(self) -> ServerInfo:
        return n.server_info(await self._json(J, "/serverInfo", operation="jira.meta.server_info"))

    # ---------------------------------------------------------------- users
    async def myself(self) -> UserRef:
        user = n.user(await self._json(J, "/myself", operation="jira.user.myself"))
        assert user is not None
        return user

    async def get_user(self, account_id: str) -> UserRef:
        user = n.user(await self._json(J, "/user", params={"accountId": account_id}, operation="jira.user.get"))
        assert user is not None
        return user

    async def search_users(self, query: str, *, limit: int = 20) -> list[UserRef]:
        data = await self._json(J, "/user/search", params={"query": query, "maxResults": limit}, operation="jira.user.search")
        return [u for u in (n.user(d) for d in data) if u]

    async def lookup_account_id(self, query: str) -> str:
        users = await self.search_users(query, limit=10)
        exact = [u for u in users if query.lower() in ((u.email or "").lower(), (u.display_name or "").lower())]
        pool = exact or users
        if not pool:
            raise NotFoundError(f"no user matches {query!r}", hint="Try the full display name or email.")
        if len(pool) > 1:
            raise AmbiguousReferenceError(f"{len(pool)} users match {query!r}", hint="Use the email or accountId.",
                                          details={"candidates": [{"account_id": u.account_id, "name": u.display_name} for u in pool]})
        return pool[0].account_id or ""

    async def assignable_users(self, *, issue_key: str | None = None, project: str | None = None,
                               query: str | None = None) -> list[UserRef]:
        if not (issue_key or project):
            raise InvalidRequestError("assignable_users needs issue_key or project", hint="Pass one of them.")
        data = await self._json(J, "/user/assignable/search", params={"issueKey": issue_key, "project": project, "query": query},
                                operation="jira.user.assignable")
        return [u for u in (n.user(d) for d in data) if u]

    # ---------------------------------------------------------------- JQL tools
    async def parse_jql(self, queries: Sequence[str], *, validation: str = "strict") -> list[JqlParseResult]:
        data = await self._json(J, "/jql/parse", method="POST", params={"validation": validation}, json={"queries": list(queries)},
                                operation="jira.jql.parse")
        return [JqlParseResult(query=q.get("query", ""), valid=not q.get("errors"), errors=list(q.get("errors", [])),
                               structure=q.get("structure")) for q in data.get("queries", [])]

    async def sanitize_jql(self, queries: Sequence[str]) -> list[str]:
        data = await self._json(J, "/jql/sanitize", method="POST", json={"queries": [{"query": q} for q in queries]},
                                operation="jira.jql.sanitize")
        return [q.get("sanitizedQuery") or q.get("initialQuery", "") for q in data.get("queries", [])]

    async def autocomplete(self, *, field_name: str | None = None, field_value: str | None = None) -> dict[str, Any]:
        if field_name:
            data = await self._json(J, "/jql/autocompletedata/suggestions", params={"fieldName": field_name, "fieldValue": field_value},
                                    operation="jira.jql.suggest")
        else:
            data = await self._json(J, "/jql/autocompletedata", operation="jira.jql.autocomplete")
        return dict(data)

    async def match_jql(self, issue_ids: Sequence[int], jqls: Sequence[str]) -> dict[str, list[int]]:
        data = await self._json(J, "/jql/match", method="POST", json={"issueIds": list(issue_ids), "jqls": list(jqls)},
                                operation="jira.jql.match")
        return {jql: [int(i) for i in m.get("matchedIssues", [])] for jql, m in zip(jqls, data.get("matches", []), strict=False)}

    async def evaluate_expression(self, expression: str, context: dict[str, Any]) -> Any:
        data = await self._json(J, "/expression/eval", method="POST", json={"expression": expression, "context": context},
                                operation="jira.expression.evaluate")
        return data.get("value")
