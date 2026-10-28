"""FakeBrowserSession: a signed-in browser over generated static HTML (design 18, layer 6).

Each canonical URL is rendered from the SAME scenario data the REST mock serves, using the page specs in ``selectors.yaml`` run backwards (primary selector ->
element), so fixtures and selectors can never disagree by accident. Drift is simulated by mutating the rendered HTML (``rename_testids`` / ``drop``), and a signed-out
profile or a bot challenge by flags. Click flows replay the equivalent REST write against the ``ScenarioServer``.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from ..backends.browser.extract import FieldSpec, PageSpec, Selectors
from ..backends.browser.session import Download, Snapshot
from ..core.errors import AtlassianError, NotFoundError
from ..core.http import HttpTransport, RequestSpec
from ..core.urls import ApiSurface
from ..formats.adf import adf_to_markdown
from .server import ScenarioServer

J, A, S, V2, V1, ADMIN = ApiSurface.JIRA_PLATFORM, ApiSurface.JIRA_AGILE, ApiSurface.JSM, ApiSurface.CONFLUENCE_V2, ApiSurface.CONFLUENCE_V1, ApiSurface.JIRA_WEBHOOKS_ADMIN
SITE = "https://example.atlassian.net"
LOGIN_HTML = "<html><body><form data-testid='login-form'><input type='password' name='password'></form></body></html>"
CHALLENGE_HTML = "<html><body><div class='g-recaptcha'></div><p>Verify you are human</p></body></html>"


def _simple(selector: str) -> tuple[str, dict[str, str]]:
    """The primary selectors of selectors.yaml are simple compounds: render them as an element carrying the attributes they match."""
    tag, attrs = "span", {}
    for part in re.findall(r"""(#[\w-]+|\.[\w-]+|\[[^\]]+\]|[A-Za-z][\w-]*)""", selector):
        if part.startswith("#"):
            attrs["id"] = part[1:]
        elif part.startswith("."):
            attrs["class"] = (attrs.get("class", "") + " " + part[1:]).strip()
        elif part.startswith("["):
            m = re.fullmatch(r"\[\s*([\w:-]+)\s*(?:=\s*(?:'([^']*)'|\"([^\"]*)\"|([^\]]*)))?\s*\]", part)
            assert m
            attrs[m.group(1)] = next((g for g in m.groups()[1:] if g is not None), "")
        else:
            tag = part
    return tag, attrs


def _open(selector: str, extra: str = "") -> tuple[str, str]:
    tag, attrs = _simple(selector)
    return f"<{tag}{''.join(f' {k}=' + chr(34) + escape(v) + chr(34) for k, v in attrs.items())}{extra}>", f"</{tag}>"


def _text(value: Any) -> str:
    if isinstance(value, dict) and value.get("type") == "doc":
        return adf_to_markdown(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _dig(obj: Any, path: str) -> Any:
    for part in path.split("."):
        obj = obj.get(part) if isinstance(obj, Mapping) else None
    return obj


def render_fields(obj: Mapping[str, Any], spec: PageSpec, fields: tuple[FieldSpec, ...], prefix: str | None) -> str:
    out = []
    for f in fields:
        full = f"{prefix}.{f.path}" if prefix else f.path
        value = _dig(obj, f.path)
        if value is None or (isinstance(value, list) and not value and f.rows):
            continue
        if f.rows:
            row_open, row_close = _open(f.rows[0])
            out.append("".join(f"{row_open}{render_fields(item, spec, f.fields, full)}{row_close}" for item in value))
            continue
        o, c = _open(spec.candidates(f.path, prefix)[0])
        values = value if isinstance(value, list) else [value]
        out.append("".join(f"{o}{_text(v) if f.type != 'html' else v}{c}" for v in values if not isinstance(v, (dict, list))))
    return "".join(out)


def render_page(spec: PageSpec, data: Any) -> str:
    ready_open, ready_close = _open(spec.ready[0]) if spec.ready else ("", "")
    if spec.rows or isinstance(data, list):
        row_open, row_close = _open(spec.rows[0] if spec.rows else f"[data-testid='{spec.name}.row']")
        return f"{ready_open}{''.join(f'{row_open}{render_fields(r, spec, spec.fields, None)}{row_close}' for r in data)}{ready_close}"
    return f"{ready_open}{render_fields(data, spec, spec.fields, None)}{ready_close}"


@dataclass
class Section:
    page: str
    data: Any


Source = Callable[[dict[str, str], dict[str, str]], Awaitable[list[Section]]]


class FakeBrowserSession:
    def __init__(self, server: ScenarioServer, *, selectors: Selectors | None = None, signed_in: bool = True, challenge: bool = False) -> None:
        from .. import AtlassianConfig, BackendKind, ClientFactory

        self.server, self.selectors, self.signed_in, self.challenge = server, selectors or Selectors.load(), signed_in, challenge
        cfg = AtlassianConfig(site="example.atlassian.net", email="ann@example.com", api_token="secret-token-value")
        self._http: HttpTransport = ClientFactory().backend(BackendKind.REST, cfg, http=server.client()).transport  # type: ignore[attr-defined]
        self.calls: list[tuple[Any, ...]] = []
        self.filled: dict[str, str] = {}
        self.attached: dict[str, tuple[str, bytes]] = {}
        self.url = ""
        self.mutate: list[Callable[[str], str]] = []                      # post-render HTML mutations (selector drift simulation)
        self.status_for: dict[str, int] = {}
        self.max_rows: int | None = None
        self.opened: list[str] = []
        self._routes: list[tuple[re.Pattern[str], Source]] = []
        self._register()

    # ------------------------------------------------------------------------------------------------ REST replay
    async def rest(self, method: str, surface: ApiSurface, path: str, *, params: dict[str, Any] | None = None, body: Any = None, files: Any = None) -> Any:
        spec = RequestSpec(method, surface, path, params or {}, json=body, files=files, headers={"X-Atlassian-Token": "no-check"} if files else {}, operation="replay")
        resp = await self._http.send(spec)
        return resp.json() if resp.content else None

    # ------------------------------------------------------------------------------------------------ routes
    def route(self, pattern: str) -> Callable[[Source], Source]:
        def deco(fn: Source) -> Source:
            self._routes.append((re.compile("^" + pattern + "$"), fn))
            return fn
        return deco

    def _register(self) -> None:
        r, rest = self.route, self.rest

        @r(r"/browse/(?P<key>[^/]+)")
        async def issue(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            k = c["key"]
            obj = await rest("GET", J, f"/issue/{k}")
            return [Section("issue.view", obj), Section("issue.comments", (await rest("GET", J, f"/issue/{k}/comment"))["comments"]),
                    Section("issue.history", (await rest("GET", J, f"/issue/{k}/changelog"))["values"]),
                    Section("issue.worklogs", (await rest("GET", J, f"/issue/{k}/worklog"))["worklogs"]),
                    Section("issue.transitions", (await rest("GET", J, f"/issue/{k}/transitions"))["transitions"]),
                    Section("issue.attachments", obj["fields"].get("attachment", []))]

        @r(r"/issues/")
        async def search(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            page = await rest("POST", J, "/search/jql", body={"jql": q.get("jql", ""), "fields": ["*navigable"], "maxResults": 100})
            return [Section("issue.row", page["issues"]), Section("issue.total", {"count": len(page["issues"])})]

        @r(r"/jira/projects")
        async def projects(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            return [Section("project.row", (await rest("GET", J, "/project/search"))["values"])]

        @r(r"/projects/(?P<key>[^/]+)(?:/components|/versions)?")
        async def project(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            k = c["key"]
            return [Section("project.view", await rest("GET", J, f"/project/{k}")), Section("project.components", await rest("GET", J, f"/project/{k}/components")),
                    Section("project.versions", await rest("GET", J, f"/project/{k}/versions"))]

        @r(r"/jira/filters")
        async def filters(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            return [Section("filter.row", (await rest("GET", J, "/filter/search"))["values"])]

        @r(r"/jira/dashboards")
        async def dashboards(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            return [Section("dashboard.row", (await rest("GET", J, "/dashboard/search"))["values"])]

        @r(r"/jira/software/boards")
        async def boards(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            return [Section("board.row", (await rest("GET", A, "/board"))["values"])]

        @r(r"/jira/software/boards/(?P<bid>[^/]+)(?:/(?P<tab>settings|issues|sprints|backlog|epics))?")
        async def board(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            b = c["bid"]
            return [Section("board.view", await rest("GET", A, f"/board/{b}")), Section("board.config", await rest("GET", A, f"/board/{b}/configuration")),
                    Section("board.issues", (await rest("GET", A, f"/board/{b}/issue"))["issues"]), Section("sprint.row", (await rest("GET", A, f"/board/{b}/sprint"))["values"]),
                    Section("board.backlog", (await rest("GET", A, f"/board/{b}/backlog"))["issues"]), Section("board.epics", (await rest("GET", A, f"/board/{b}/epic"))["values"])]

        @r(r"/jira/software/sprints/(?P<sid>[^/]+)(?:/issues)?")
        async def sprint(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            s = c["sid"]
            return [Section("sprint.view", await rest("GET", A, f"/sprint/{s}")), Section("sprint.issues", (await rest("GET", A, f"/sprint/{s}/issue"))["issues"])]

        @r(r"/servicedesk/about")
        async def jsm_info(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            return [Section("jsm.info", await rest("GET", S, "/info"))]

        @r(r"/servicedesk/desks")
        async def desks(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            return [Section("jsm.desk", (await rest("GET", S, "/servicedesk"))["values"])]

        @r(r"/servicedesk/(?P<did>[^/]+)/request-types")
        async def request_types(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            return [Section("jsm.requesttype", (await rest("GET", S, f"/servicedesk/{c['did']}/requesttype"))["values"])]

        @r(r"/servicedesk/requests")
        async def requests_(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            params = {"serviceDeskId": q.get("desk") or None, "requestStatus": q.get("status") or None}
            return [Section("jsm.request.row", (await rest("GET", S, "/request", params=params))["values"])]

        @r(r"/servicedesk/requests/(?P<key>[^/]+)")
        async def request(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            k = c["key"]
            return [Section("jsm.request.view", await rest("GET", S, f"/request/{k}")), Section("jsm.comments", (await rest("GET", S, f"/request/{k}/comment"))["values"]),
                    Section("jsm.sla", (await rest("GET", S, f"/request/{k}/sla"))["values"])]

        @r(r"/servicedesk/kb")
        async def kb(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            return [Section("jsm.kb", (await rest("GET", S, "/knowledgebase/article", params={"query": q.get("query", "")}))["values"])]

        @r(r"/servicedesk/organizations")
        async def orgs(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            return [Section("jsm.org", (await rest("GET", S, "/organization"))["values"])]

        @r(r"/servicedesk/(?P<did>[^/]+)/customers")
        async def customers(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            return [Section("jsm.customer", (await rest("GET", S, f"/servicedesk/{c['did']}/customer"))["values"])]

        @r(r"/wiki/spaces/-/pages/(?P<pid>[^/]+)(?P<tab>/children|/ancestors|/restrictions|/edit)?")
        async def page(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            p = c["pid"]
            obj = await rest("GET", V2, f"/pages/{p}", params={"body-format": "storage"})
            out = [Section("conf.page.view", obj),
                   Section("conf.macros", {"id": obj["id"], "title": obj["title"], "body": {"view": {"value": (await rest("GET", V2, f"/pages/{p}", params={"body-format": "view"}))
                                                                                                     .get("body", {}).get("view", {}).get("value", obj["body"]["storage"]["value"])}}}),
                   Section("conf.comments.footer", (await rest("GET", V2, f"/pages/{p}/footer-comments", params={"body-format": "storage"}))["results"]),
                   Section("conf.comments.inline", (await rest("GET", V2, f"/pages/{p}/inline-comments", params={"body-format": "storage"}))["results"]),
                   Section("conf.labels", (await rest("GET", V2, f"/pages/{p}/labels"))["results"]),
                   Section("conf.children", (await rest("GET", V2, f"/pages/{p}/children"))["results"]),
                   Section("conf.ancestors", (await rest("GET", V2, f"/pages/{p}/ancestors"))["results"])]
            res = await rest("GET", V1, f"/content/{p}/restriction/byOperation")
            out.append(Section("conf.restrictions", [{"operation": op, "users": [u.get("displayName") for u in v["restrictions"]["user"]["results"]],
                                                      "groups": [g["name"] for g in v["restrictions"]["group"]["results"]]} for op, v in res.items()]))
            return out

        @r(r"/wiki/pages/(?P<pid>[^/]+)/history")
        async def history(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            return [Section("conf.versions", (await rest("GET", V2, f"/pages/{c['pid']}/versions"))["results"])]

        @r(r"/wiki/pages")
        async def pages(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            return [Section("conf.page.row", (await rest("GET", V2, "/pages", params={"title": q.get("title") or None}))["results"])]

        @r(r"/wiki/spaces/(?P<key>[^/]+)/overview")
        async def space(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            return [Section("conf.space.view", await rest("GET", V2, f"/spaces/{c['key']}"))]

        @r(r"/wiki/spaces")
        async def spaces(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            return [Section("conf.space.row", (await rest("GET", V2, "/spaces"))["results"])]

        @r(r"/wiki/search")
        async def conf_search(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            rows = (await rest("GET", V1, "/search", params={"cql": q.get("cql", "")}))["results"]
            return [Section("conf.search", rows)]

        @r(r"/wiki/pages/viewpageattachments.action")
        async def attachments(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            return [Section("conf.attachments", (await rest("GET", V2, f"/pages/{q['pageId']}/attachments"))["results"])]

        @r(r"/wiki/attachment/(?P<aid>[^/]+)")
        async def attachment(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            return [Section("conf.attachment.meta", await rest("GET", V2, f"/attachments/{c['aid']}"))]

        @r(r"/plugins/servlet/webhooks")
        async def webhooks(c: dict[str, str], q: dict[str, str]) -> list[Section]:
            return [Section("webhook.row", await rest("GET", ADMIN, "/webhook"))]

    # ------------------------------------------------------------------------------------------------ forms (always present on the issue page etc.)
    @staticmethod
    def _forms(path: str) -> str:
        if path.startswith("/browse/"):
            return ("<textarea data-testid='comment.input'></textarea><button data-testid='comment.submit'>Add</button><input data-testid='summary.input'>"
                    "<button data-testid='summary.save'>Save</button><button data-transition-id='31'>Done</button><input data-testid='link.type'><input data-testid='link.issue'>"
                    "<button data-testid='link.submit'>Link</button><input type='file' data-testid='attach.input'><button data-testid='attach.submit'>Attach</button>")
        if path.startswith("/jira/software/sprints/"):
            return "<input data-testid='sprint.add.input'><button data-testid='sprint.add.submit'>Add</button>"
        if path.startswith("/servicedesk/requests/"):
            return "<textarea data-testid='jsm.comment.input'></textarea><input data-testid='jsm.comment.visibility'><button data-testid='jsm.comment.submit'>Send</button>"
        if path.endswith("/edit"):
            return "<input data-testid='editor.title'><textarea data-testid='editor.body'></textarea><input data-testid='editor.version'><button data-testid='editor.publish'>Publish</button>"
        return ""

    # ------------------------------------------------------------------------------------------------ BrowserSession
    async def _render(self, url: str) -> Snapshot:
        parts = urlsplit(url)
        q = dict(parse_qsl(parts.query))
        path = unquote(parts.path)
        if not self.signed_in:
            return Snapshot("https://id.atlassian.com/login?continue=" + url, 200, LOGIN_HTML)
        if self.challenge:
            return Snapshot(url, 200, CHALLENGE_HTML)
        if url in self.status_for:
            return Snapshot(url, self.status_for[url], "<html><body>error</body></html>")
        for pat, source in self._routes:
            m = pat.match(path)
            if m:
                try:
                    sections = await source(m.groupdict(), q)
                except NotFoundError:
                    return Snapshot(url, 404, "<html><body><h1>We couldn't find that</h1></body></html>")
                except AtlassianError as exc:
                    return Snapshot(url, 403, f"<html><body>{escape(str(exc))}</body></html>")
                body = "".join(render_page(self.selectors.page(s.page), self._cap(s.data)) for s in sections)
                html = f"<html><body><main>{body}{self._forms(path)}</main></body></html>"
                for fn in self.mutate:
                    html = fn(html)
                return Snapshot(url, 200, html)
        return Snapshot(url, 404, "<html><body>unknown page</body></html>")

    def _cap(self, data: Any) -> Any:
        return data[: self.max_rows] if self.max_rows is not None and isinstance(data, list) else data

    async def open(self, url: str) -> Snapshot:
        self.calls.append(("open", url))
        self.opened.append(url)
        self.url = url
        return await self._render(url)

    async def fill(self, selector: str, value: str) -> None:
        self.calls.append(("fill", selector))                              # the value is deliberately not recorded: it may be sensitive
        self.filled[selector] = value

    async def attach(self, selector: str, name: str, data: bytes) -> None:
        self.calls.append(("attach", selector, name))
        self.attached[selector] = (name, data)

    async def click(self, selector: str) -> Snapshot:
        self.calls.append(("click", selector))
        path = urlsplit(self.url).path
        f = self.filled
        try:
            if selector == "[data-testid='comment.submit']":
                await self.rest("POST", J, f"/issue/{path.rsplit('/', 1)[-1]}/comment", body={"body": f["[data-testid='comment.input']"]})
            elif selector.startswith("[data-transition-id="):
                await self.rest("POST", J, f"/issue/{path.rsplit('/', 1)[-1]}/transitions", body={"transition": {"id": re.search(r"'(\w+)'", selector).group(1)}})  # type: ignore[union-attr]
            elif selector == "[data-testid='summary.save']":
                await self.rest("PUT", J, f"/issue/{path.rsplit('/', 1)[-1]}", body={"fields": {"summary": f["[data-testid='summary.input']"]}})
            elif selector == "[data-testid='link.submit']":
                await self.rest("POST", J, "/issueLink", body={"type": {"name": f["[data-testid='link.type']"]}, "inwardIssue": {"key": path.rsplit("/", 1)[-1]},
                                                              "outwardIssue": {"key": f["[data-testid='link.issue']"]}})
            elif selector == "[data-testid='attach.submit']":
                name, data = self.attached["input[type='file'][data-testid='attach.input']"]
                await self.rest("POST", J, f"/issue/{path.rsplit('/', 1)[-1]}/attachments", files={"file": (name, data)})
            elif selector == "[data-testid='sprint.add.submit']":
                await self.rest("POST", A, f"/sprint/{path.split('/')[4]}/issue", body={"issues": f["[data-testid='sprint.add.input']"].split(",")})
            elif selector == "[data-testid='jsm.comment.submit']":
                await self.rest("POST", S, f"/request/{path.rsplit('/', 1)[-1]}/comment", body={"body": f["[data-testid='jsm.comment.input']"], "public": f["[data-testid='jsm.comment.visibility']"] == "public"})
            elif selector == "[data-testid='editor.publish']":
                pid = path.split("/")[5]
                await self.rest("PUT", V2, f"/pages/{pid}", body={"id": pid, "status": "current", "title": f["[data-testid='editor.title']"],
                                                                 "body": {"representation": "storage", "value": f["[data-testid='editor.body']"]},
                                                                 "version": {"number": int(f["[data-testid='editor.version']"])}})
            else:
                raise KeyError(selector)
        finally:
            self.filled.clear()
            self.attached.clear()
        return await self._render(self.url)

    async def download(self, url: str) -> Download:
        self.calls.append(("download", url))
        parts = urlsplit(url)
        q = dict(parse_qsl(parts.query))
        path = parts.path
        if not self.signed_in:
            from ..core.errors import AuthenticationError

            raise AuthenticationError("not signed in", hint="A human must sign in.")
        if m := re.match(r"^/secure/attachment/([^/]+)/$", path):
            meta = await self.rest("GET", J, f"/attachment/{m.group(1)}")
            data = (await self._http.send(RequestSpec("GET", J, f"/attachment/content/{m.group(1)}", operation="replay"))).content
            return Download(data, meta["filename"], meta.get("mimeType"))
        if path.startswith("/wiki/download/attachments/") or path.startswith("/download/attachments/"):
            data = (await self._http.send(RequestSpec("GET", V2, SITE + "/wiki" + path.removeprefix("/wiki"), operation="replay"))).content
            return Download(data, path.rsplit("/", 1)[-1])
        if path.startswith("/sr/jira.issueviews:searchrequest-csv"):
            page = await self.rest("POST", J, "/search/jql", body={"jql": q["jqlQuery"], "fields": ["*navigable"], "maxResults": 100})
            rows = ["key,summary,status"] + [f"{i['key']},{i['fields']['summary']},{i['fields']['status']['name']}" for i in page["issues"]]
            return Download("\n".join(rows).encode(), "SearchRequest.csv", "text/csv")
        if path == "/wiki/spaces/flyingpdf/pdfpageexport.action":
            return Download(b"%PDF-1.7 fake export of page " + q["pageId"].encode(), "page.pdf", "application/pdf")
        if path == "/wiki/exportword":
            return Download(b"PK-fake-docx-" + q["pageId"].encode(), "page.doc", "application/msword")
        if path == "/wiki/spaces/exportspace.action":
            return Download(f"space-export:{q['key']}:{q.get('format', 'html')}".encode(), "space.zip", "application/zip")
        raise NotFoundError(f"no download at {url}")

    async def screenshot(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n fake screenshot of " + self.url.encode())
        return path

    async def aclose(self) -> None:
        self.calls.append(("close",))

    # ------------------------------------------------------------------------------------------------ drift helpers
    def rename_testids(self, mapping: Mapping[str, str]) -> None:
        """Simulate a UI release that renamed data-testid values."""
        def mutate(html: str) -> str:
            for old, new in mapping.items():
                html = html.replace(f"data-testid=\"{old}\"", f"data-testid=\"{new}\"")
            return html
        self.mutate.append(mutate)

    def use_legacy_markup(self, old: str, new_attr: str) -> None:
        """Replace ``data-testid="old"`` with another attribute (so only a fallback selector can still find it)."""
        self.mutate.append(lambda html: html.replace(f"data-testid=\"{old}\"", new_attr))

