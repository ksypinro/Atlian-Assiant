"""Jira Data Center REST (``/rest/api/2``): the Cloud components with the differences handled (design 10.2).

Differences: offset-paged ``/search`` (no ``/search/jql``), users by ``username``/``key`` (no accountId), wiki-markup rich text, no JQL parse/sanitize/match,
no ``/project/search`` or ``/filter/search``-style paging everywhere, and attachment content under ``/secure/attachment``.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ...core.errors import DeploymentMismatchError, InvalidRequestError, NotFoundError, UnsupportedOperationError
from ...core.http import RequestSpec
from ...core.pagination import Cursor
from ...core.urls import ApiSurface
from ...formats.wiki import markdown_to_wiki, wiki_to_markdown
from ...models.common import BodyInput, Page, UserRef
from ...models.jira import AttachmentContent, Comment, Dashboard, Field, Filter, Issue, Project
from ..rest_cloud import normalize as n
from ..rest_cloud.bodies import body_text
from ..rest_cloud.jira import JiraRest
from ..rest_cloud.jira_write import JiraWriteRest

J = ApiSurface.JIRA_PLATFORM


def _slice(items: list[Any], limit: int, cursor: str | None, kind: Any, strategy: str) -> Page[Any]:
    start = int(Cursor.decode(cursor).state["i"]) if cursor else 0
    chunk = items[start:start + limit]
    nxt = start + limit if start + limit < len(items) else None
    return Page(items=chunk, next_cursor=Cursor(kind, strategy, {"i": nxt}).encode() if nxt is not None else None, total=len(items))


class JiraDcRest(JiraRest):
    @staticmethod
    def _md(issue: Issue) -> Issue:
        """DC returns wiki markup in descriptions; the normalized model carries Markdown."""
        return issue.model_copy(update={"description_markdown": wiki_to_markdown(issue.description_markdown) or None}) if issue.description_markdown else issue

    async def get_issue(self, key: str, *, fields: Sequence[str] | None = None, expand: Sequence[str] = ()) -> Issue:
        return self._md(await super().get_issue(key, fields=fields, expand=expand))

    async def list_comments(self, key: str, *, limit: int = 50, cursor: str | None = None) -> Page[Comment]:
        page = await super().list_comments(key, limit=limit, cursor=cursor)
        return page.model_copy(update={"items": [c.model_copy(update={"body_markdown": wiki_to_markdown(c.body_markdown)}) if c.body_markdown else c for c in page.items]})

    # ---------------------------------------------------------------- search
    async def search_issues(self, jql: str, *, fields: Sequence[str] | None = None, limit: int = 50, cursor: str | None = None,
                            reconcile_issue_ids: Sequence[int] = ()) -> Page[Issue]:
        if reconcile_issue_ids:
            raise UnsupportedOperationError("reconcile_issue_ids is a Cloud search option", operation="jira.issue.search",
                                            hint="Data Center search is strongly consistent: simply search again.")
        start = int(Cursor.decode(cursor).state["startAt"]) if cursor else 0
        body = {"jql": jql, "startAt": start, "maxResults": limit, "fields": list(fields) if fields else ["*navigable"]}
        data = await self._json(J, "/search", method="POST", json=body, operation="jira.issue.search", idempotent=True)
        items = [self._md(n.issue(d, self.site_url)) for d in data.get("issues", [])]
        total = int(data.get("total", start + len(items)))
        nxt = start + len(items)
        return Page(items=items, next_cursor=Cursor(self.kind, "dc_search", {"startAt": nxt}).encode() if items and nxt < total else None, total=total)

    async def count_issues(self, jql: str) -> int:
        data = await self._json(J, "/search", method="POST", json={"jql": jql, "maxResults": 0, "fields": ["key"]}, operation="jira.issue.count", idempotent=True)
        return int(data["total"])

    async def download_attachment(self, attachment_id: str, *, dest: Path | None = None, max_bytes: int | None = None, extract_text: bool = False) -> AttachmentContent:
        meta = await self._json(J, f"/attachment/{attachment_id}", operation="jira.issue.attachment.download")
        size = int(meta.get("size") or 0)
        if max_bytes is not None and size > max_bytes:
            from ...core.errors import error_for_status

            raise error_for_status(413, f"attachment is {size} bytes, above max_bytes={max_bytes}", operation="jira.issue.attachment.download")
        data = await self.transport.stream_download(RequestSpec("GET", J, str(meta["content"]), headers={"Accept": "*/*"}, operation="jira.issue.attachment.download"),
                                                    max_bytes=max_bytes)
        mime = meta.get("mimeType")
        text = data.decode("utf-8", errors="replace") if extract_text and (mime or "").startswith("text/") else None
        path = None
        if dest is not None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            path = str(dest)
        return AttachmentContent(attachment_id=str(attachment_id), filename=meta.get("filename"), mime_type=mime, size=len(data), data=None if dest else data, path=path, text=text)

    # ---------------------------------------------------------------- projects / filters / dashboards (plain arrays on DC)
    async def list_projects(self, *, query: str | None = None, limit: int = 50, cursor: str | None = None) -> Page[Project]:
        data = await self._json(J, "/project", operation="jira.project.list")
        projects = [n.project(p) for p in data if not query or query.lower() in (p.get("name", "") + p.get("key", "")).lower()]
        return _slice(projects, limit, cursor, self.kind, "dc_list")

    async def list_filters(self, *, query: str | None = None, limit: int = 50, cursor: str | None = None) -> Page[Filter]:
        data = await self._json(J, "/filter/favourite", operation="jira.filter.list")
        filters = [n.jfilter(f) for f in data if not query or query.lower() in f.get("name", "").lower()]
        return _slice(filters, limit, cursor, self.kind, "dc_list")

    async def list_dashboards(self, *, limit: int = 50, cursor: str | None = None) -> Page[Dashboard]:
        data = await self._json(J, "/dashboard", operation="jira.dashboard.list")
        return _slice([n.dashboard(d) for d in data.get("dashboards", [])], limit, cursor, self.kind, "dc_list")

    async def get_create_fields(self, project: str, issue_type: str) -> list[Field]:
        types = await self.list_issue_types(project)
        match = [t for t in types if issue_type in (t.id, t.name) or t.name.lower() == issue_type.lower()]
        if not match:
            raise NotFoundError(f"issue type {issue_type!r} not found in project {project}", hint="Valid types: " + ", ".join(t.name for t in types))
        data = await self._json(J, "/issue/createmeta", params={"projectKeys": project, "issuetypeIds": match[0].id, "expand": "projects.issuetypes.fields"},
                                operation="jira.meta.create_fields")
        fields = (((data.get("projects") or [{}])[0].get("issuetypes") or [{}])[0].get("fields")) or {}
        return [n.jfield({"id": fid, "name": f.get("name"), "schema": f.get("schema"), "custom": fid.startswith("customfield_"), "required": f.get("required")})
                for fid, f in fields.items()]

    # ---------------------------------------------------------------- users: username / key instead of accountId
    async def get_user(self, account_id: str) -> UserRef:
        user = n.user(await self._json(J, "/user", params={"username": account_id}, operation="jira.user.get"))
        assert user is not None
        return user

    async def search_users(self, query: str, *, limit: int = 20) -> list[UserRef]:
        data = await self._json(J, "/user/search", params={"username": query, "maxResults": limit}, operation="jira.user.search")
        return [u for u in (n.user(d) for d in data) if u]

    async def lookup_account_id(self, query: str) -> str:
        """Data Center has no accountId: the stable identity is the username. Returns it for an exact name/email/display-name match."""
        users = await self.search_users(query, limit=10)
        exact = [u for u in users if query.lower() in ((u.email or "").lower(), (u.display_name or "").lower(), (u.username or "").lower())]
        pool = exact or users
        if not pool:
            raise NotFoundError(f"no user matches {query!r}", hint="Try the username or email.")
        if len(pool) > 1:
            from ...core.errors import AmbiguousReferenceError
            raise AmbiguousReferenceError(f"{len(pool)} users match {query!r}", hint="Use the username.", details={"candidates": [{"username": u.username, "name": u.display_name} for u in pool]})
        return pool[0].username or ""

    async def assignable_users(self, *, issue_key: str | None = None, project: str | None = None, query: str | None = None) -> list[UserRef]:
        if not (issue_key or project):
            raise InvalidRequestError("assignable_users needs issue_key or project", hint="Pass one of them.")
        data = await self._json(J, "/user/assignable/search", params={"issueKey": issue_key, "project": project, "username": query or ""}, operation="jira.user.assignable")
        return [u for u in (n.user(d) for d in data) if u]

    # ---------------------------------------------------------------- JQL tools: only the autocomplete data exists on Data Center
    async def parse_jql(self, queries: Sequence[str], *, validation: str = "strict") -> Any:
        raise UnsupportedOperationError("JQL parse is a Cloud-only endpoint", operation="jira.jql.parse", hint="Validate by running a search with maxResults=0 (count_issues).")

    async def sanitize_jql(self, queries: Sequence[str]) -> Any:
        raise UnsupportedOperationError("JQL sanitize is a Cloud-only endpoint", operation="jira.jql.sanitize", hint="Data Center JQL uses usernames; there is nothing to sanitize.")

    async def match_jql(self, issue_ids: Sequence[int], jqls: Sequence[str]) -> Any:
        raise UnsupportedOperationError("JQL match is a Cloud-only endpoint", operation="jira.jql.match", hint="Search with `issue in (...) AND (<jql>)` instead.")

    async def evaluate_expression(self, expression: str, context: dict[str, Any]) -> Any:
        raise DeploymentMismatchError("Jira expressions are Cloud-only", operation="jira.expression.evaluate", hint="Use JQL.")


class JiraDcWrite(JiraWriteRest):
    """Writes with wiki-markup bodies and username-based user references."""

    def _rich(self, body: BodyInput | str) -> Any:
        b = body if isinstance(body, BodyInput) else BodyInput(format="markdown", value=body)
        if b.format == "wiki":
            return str(b.value)
        text = body_text(b)
        return text if b.format == "plain" else markdown_to_wiki(text)

    def _user_ref(self, resolved: str | None) -> dict[str, Any]:
        return {"name": resolved}

    def _watch_arg(self, who: str) -> dict[str, str]:
        return {"username": who}

    async def _me(self) -> str:
        return str((await self._reader.myself()).username)

    async def create_project(self, key: str, name: str, *, template: str, lead: str) -> Project:
        body = {"key": key, "name": name, "projectTypeKey": "software", "projectTemplateKey": template, "lead": await self._users.resolve(lead)}
        data = await self._write("POST", "/project", op="jira.project.create", json=body, idempotent=False)
        return Project(id=str(data["id"]), key=data.get("key", key), name=name, raw=data)

    async def list_workflows(self, *, project: str | None = None) -> Any:
        from ...models.jira import Workflow
        data = await self._json(J, "/workflow", operation="jira.admin.workflows.list")
        return [Workflow(id=str(w.get("name", "")), name=w.get("name", ""), statuses=[], raw=w) for w in data]

    async def get_screen(self, screen_id: str) -> Any:
        raise UnsupportedOperationError("screens cannot be read through the Data Center REST API", operation="jira.admin.screen.get", hint="Use the admin UI.")

    async def update_screen(self, screen_id: str, **changes: Any) -> Any:
        raise UnsupportedOperationError("screens cannot be changed through the Data Center REST API", operation="jira.admin.screen.update", hint="Use the admin UI.")
