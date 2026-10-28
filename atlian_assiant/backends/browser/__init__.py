"""BROWSER backend: Playwright UI automation over a human-signed-in persistent profile (design 10.8).

Read-only by default; the lowest priority in every preset; concurrency 1. A human completes SSO / MFA: the client never types credentials and never solves a
CAPTCHA. It reads the rendered UI through ``selectors.yaml`` (with fallbacks, drift detected) and uses the product's NATIVE exports; it never calls a private
XHR or GraphQL endpoint seen in DevTools. Every result carries ``source.url``; infinite-scroll lists report ``Page.total=None``.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

from ...capabilities import Access, BackendKind, Deployment, Operation
from ...config import AtlassianConfig
from ...core.errors import (
    BackendUnavailableError,
    InvalidRequestError,
    NotFoundError,
    PolicyDeniedError,
    UnsupportedOperationError,
)
from ...core.http import RequestSpec
from ...core.services import CoreServices
from ...factory import Availability
from ...matrix import declared_operations
from ...models.common import AtlassianModel, Page, SourceRef
from ...policy import AccessPolicy
from ...protocols.base import BackendHealth
from ..cli.backend import CliJiraWrite
from ..cli.commandmap import shape
from ..optransport import Handler, OpBackend, OpTransport
from ..rest_cloud.agile import AgileRest
from ..rest_cloud.agile_write import AgileWriteRest
from ..rest_cloud.confluence import ConfluenceRest
from ..rest_cloud.confluence_write import ConfluenceWriteRest
from ..rest_cloud.jira import JiraRest
from ..rest_cloud.jsm import JsmRest
from ..rest_cloud.jsm_write import JsmWriteRest
from ..rest_cloud.webhooks import WebhookRest
from .extract import Selectors, extract_object, extract_rows
from .ops import BINDINGS, WRITE_OPS, Read, Write, captures, query_vars, render_url
from .session import PERSONAL_PROFILES, BrowserSession, PlaywrightSession, Snapshot, check_page

#: ◐ rows (design section 11; ``*`` = needs allow_browser_writes + confirmation): the subset the UI flows serve
PARTIAL_OPS: dict[str, tuple[str, ...]] = {
    "jira-changelog": ("jira.issue.changelog.list",),
    "jira-worklogs": ("jira.issue.worklogs.list",),
    "jira-transitions-list": ("jira.issue.transitions.list",),
    "jira-issue-create-edit": ("jira.issue.edit",),
    "jira-transition": ("jira.issue.transition",),
    "jira-comment-worklog-add": ("jira.issue.comment.add",),
    "jira-links-write": ("jira.issue.link.create",),
    "jira-attachment-upload": ("jira.issue.attachment.upload",),
    "agile-write": ("jira.agile.issue.move_to_sprint",),
    "jsm-write": ("jsm.request.comment.add",),
    "conf-tree": ("confluence.content.children", "confluence.content.ancestors"),
    "conf-versions-diff": ("confluence.content.versions",),
    "conf-permissions": ("confluence.permission.restrictions.get",),
    "conf-content-write": ("confluence.content.update",),
    "webhook-management": ("webhook.jira.admin.list",),
}


@dataclass
class Evidence:
    op: str
    url: str
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    screenshot: Path | None = None
    incomplete: bool = False


class BrowserOpTransport(OpTransport):
    def __init__(self, session: BrowserSession, *, site: str, selectors: Selectors | None = None, allow_writes: bool = False, screenshot_dir: Path | None = None,
                 ops: frozenset[str] | None = None, max_rows: int = 500) -> None:
        self.session, self.base, self.selectors = session, f"https://{site.removeprefix('https://').rstrip('/')}", selectors or Selectors.load()
        self.allow_writes, self.screenshot_dir, self.max_rows = allow_writes, screenshot_dir, max_rows
        self.evidence: list[Evidence] = []
        self._jira_downloads: dict[str, bytes] = {}
        self.bindings: dict[str, list[Read | Write]] = {}
        for b in BINDINGS:
            if ops is None or b.op in ops:
                self.bindings.setdefault(b.op, []).append(b)
        handlers: dict[str, Handler] = {op: self._handler(op) for op in self.bindings}
        super().__init__("browser", handlers, site=site, hint="The browser backend only reads the rendered UI and uses native exports; the router falls back to REST or MCP.")

    @property
    def last_url(self) -> str | None:
        return self.evidence[-1].url if self.evidence else None

    def _handler(self, op: str) -> Handler:
        async def handle(spec: RequestSpec) -> Any:
            if any(isinstance(b, Read) and b.download for b in self.bindings[op]):                 # second request of a download: the bytes
                if spec.path.startswith("/attachment/content/"):
                    return self._jira_downloads.pop(spec.path.rsplit("/", 1)[-1], b"")
                if spec.path.startswith("http"):
                    dl = await self.session.download(spec.path)
                    self.evidence.append(Evidence(op, spec.path))
                    return dl.data
            for b in self.bindings[op]:
                caps = captures(b.path, spec.path)
                if caps is None:
                    continue
                return await (self._write(b, spec, caps) if isinstance(b, Write) else self._read(b, spec, caps))
            raise UnsupportedOperationError(f"{op}: no page flow for {spec.path}", operation=op, backend="browser", hint="The router falls back to REST or MCP.")
        return handle

    async def _goto(self, op: str, url: str) -> Snapshot:
        snap = check_page(await self.session.open(url))
        if snap.status == 404:
            raise NotFoundError(f"{url} returned 404", hint="Not found OR you lack permission to see it. Verify the key/id by searching.", operation=op, backend="browser")
        shot = None
        if self.screenshot_dir is not None:
            shot = await self.session.screenshot(self.screenshot_dir / f"{len(self.evidence) + 1:04d}-{op}.png")
        self.evidence.append(Evidence(op, snap.url, screenshot=shot))
        return snap

    async def _read(self, b: Read, spec: RequestSpec, caps: dict[str, str]) -> Any:
        bad = [p for p in b.unsupported_params if spec.params.get(p) is not None]
        if bad:
            raise UnsupportedOperationError(f"{b.op}: the UI page cannot honour {', '.join(bad)}", operation=b.op, backend="browser",
                                            hint="Historic versions are not rendered by the page; use REST or MCP.")
        values: dict[str, Any] = {**query_vars(spec), **caps}
        url = self.base + render_url(b.url, values)
        if b.download:
            return await self._download(b, spec, caps, url)
        snap = await self._goto(b.op, url)
        page = self.selectors.page(b.page)
        if b.kind == "object":
            return extract_object(snap.html, page)
        rows = extract_rows(snap.html, page)
        if len(rows) >= self.max_rows:
            self.evidence[-1].incomplete = True                      # an infinite-scroll list may hold more than was loaded
        rows = rows[: self.max_rows]
        if b.adapt:
            rows = b.adapt(rows, spec)
        if isinstance(rows, dict):
            return rows
        out = shape(b.wrap, rows, spec)
        if isinstance(out, dict):
            for k in ("total", "totalSize"):
                out.pop(k, None)                                     # a scrolled UI list has no reliable total: Page.total stays None
        return out

    async def _download(self, b: Read, spec: RequestSpec, caps: dict[str, str], url: str) -> Any:
        if b.page:                                                                  # Confluence: the metadata page first (it names the download link)
            snap = await self._goto(b.op, url)
            return extract_object(snap.html, self.selectors.page(b.page))
        dl = await self.session.download(url)                                       # Jira: the secure URL downloads directly
        self.evidence.append(Evidence(b.op, url))
        ident = caps["attachment_id"]
        self._jira_downloads[ident] = dl.data
        return {"id": ident, "filename": dl.filename or ident, "size": len(dl.data), "mimeType": dl.mime_type or "application/octet-stream"}

    async def _write(self, b: Write, spec: RequestSpec, caps: dict[str, str]) -> Any:
        if not self.allow_writes:
            raise PolicyDeniedError(f"{b.op}: browser writes are disabled", hint="Set allow_browser_writes=True AND approve the confirmation; prefer REST or MCP for writes.",
                                    operation=b.op, backend="browser")
        values: dict[str, Any] = {**query_vars(spec), **caps}
        if "key" not in values and isinstance(spec.json, dict):
            values["key"] = ((spec.json.get("inwardIssue") or {}).get("key") or "")
        steps = b.steps(spec, caps)
        snap = await self._goto(b.op, self.base + render_url(b.url, values))
        before = {r.get("id") for r in extract_rows(snap.html, self.selectors.page(b.result_page))} if b.result in ("last", "list") and b.result_page else set()
        for step in steps:
            if step[0] == "fill":
                await self.session.fill(step[1], step[2])
            elif step[0] == "attach":
                await self.session.attach(step[1], step[2], step[3])
            elif step[0] == "click":
                snap = check_page(await self.session.click(step[1]))
        if b.result_page is None:
            return None
        page = self.selectors.page(b.result_page)
        if b.result == "object":
            return extract_object(snap.html, page)
        rows = extract_rows(snap.html, page)
        rows = [r for r in rows if r.get("id") not in before]                    # only what the flow created
        if b.result == "last":
            if not rows:
                raise InvalidRequestError(f"{b.op}: the UI did not show the created item", hint="Check the page in the browser profile.", operation=b.op)
            return rows[-1]
        return rows


class StampedPart:
    """Attaches ``source.url`` (the page it was read from) to every model a part returns, so results carry their evidence (design 10.8)."""

    def __init__(self, inner: object, transport: BrowserOpTransport) -> None:
        self._inner, self._transport = inner, transport

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        async def call(*a: Any, **kw: Any) -> Any:
            before = len(self._transport.evidence)
            result = await attr(*a, **kw)
            if len(self._transport.evidence) > before:
                ev = self._transport.evidence[-1]
                ref = SourceRef(backend=BackendKind.BROWSER, site=self._transport.site if hasattr(self._transport, "site") else None, url=ev.url, fetched_at=ev.fetched_at)
                _stamp(result, ref)
            return result
        return call if _is_coro(attr) else attr


def _is_coro(fn: Callable[..., Any]) -> bool:
    import inspect

    return inspect.iscoroutinefunction(fn)


def _stamp(result: Any, ref: SourceRef) -> None:
    if isinstance(result, Page):
        result.source = ref
        for item in result.items:
            _stamp(item, ref)
    elif isinstance(result, list):
        for item in result:
            _stamp(item, ref)
    elif isinstance(result, AtlassianModel) and result.source is None:
        result.source = ref


class BrowserExporter:
    """``JiraExporter`` / ``ConfluenceExporter`` through the product's NATIVE exports (CSV, PDF, Word, space export) downloaded with the signed-in session."""

    def __init__(self, transport: BrowserOpTransport) -> None:
        self.t = transport

    async def _save(self, op: str, url: str, dest: Path) -> Path:
        dl = await self.t.session.download(self.t.base + url)
        self.t.evidence.append(Evidence(op, self.t.base + url))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(dl.data)
        return dest

    async def export_issues_csv(self, jql: str, *, fields: Any = None, dest: Path) -> Path:
        return await self._save("export.jira.csv", f"/sr/jira.issueviews:searchrequest-csv-current-fields/temp/SearchRequest.csv?jqlQuery={render_url('{q}', {'q': jql})}", dest)

    async def export_content(self, content_id: str, *, format: Literal["pdf", "word", "html", "markdown"], dest: Path) -> Path:
        if format == "pdf":
            return await self._save("export.confluence.page", f"/wiki/spaces/flyingpdf/pdfpageexport.action?pageId={content_id}", dest)
        if format == "word":
            return await self._save("export.confluence.page", f"/wiki/exportword?pageId={content_id}", dest)
        if format == "html":
            snap = await self.t._goto("export.confluence.page", f"{self.t.base}/wiki/spaces/-/pages/{content_id}")
            from .dom import parse

            node = next(iter(parse(snap.html).select("[data-testid='conf.page.body'], #main-content .wiki-content")), None)
            if node is None:
                raise NotFoundError(f"page {content_id} has no content area", hint="Not found OR you lack permission to see it.")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(f"<!doctype html><html><body>{node.inner_html()}</body></html>", encoding="utf-8")
            return dest
        raise UnsupportedOperationError("markdown export is not a native Confluence export", operation="export.confluence.page",
                                        hint="REST synthesizes Markdown; the browser backend offers pdf, word and html.")

    async def export_space(self, space_key: str, *, format: Literal["pdf", "html", "xml", "csv"], dest: Path) -> Path:
        if format == "csv":
            raise UnsupportedOperationError("space export as csv does not exist natively", operation="export.confluence.space", hint="Use html, xml or pdf.")
        return await self._save("export.confluence.space", f"/wiki/spaces/exportspace.action?key={space_key}&format={format}", dest)


class BrowserBackend(OpBackend):
    kind: ClassVar[BackendKind] = BackendKind.BROWSER
    DIRECT: ClassVar[frozenset[str]] = frozenset({"export.jira.csv", "export.confluence.page", "export.confluence.space"})

    def __init__(self, session: BrowserSession, *, site: str, selectors: Selectors | None = None, allow_writes: bool = False, screenshot_dir: Path | None = None) -> None:
        declared = declared_operations(self.kind, PARTIAL_OPS)
        if not allow_writes:
            declared = frozenset(o for o in declared if o not in WRITE_OPS)
        transport = BrowserOpTransport(session, site=site, selectors=selectors, allow_writes=allow_writes, screenshot_dir=screenshot_dir)
        self.session, self.browser_transport, self.allow_writes = session, transport, allow_writes
        jira = JiraRest(transport)
        confluence = ConfluenceRest(transport)
        self.exports = BrowserExporter(transport)
        raw_parts: tuple[object, ...] = (jira, AgileRest(transport), JsmRest(transport), confluence, CliJiraWrite(transport, jira), AgileWriteRest(transport), JsmWriteRest(transport),
                                         ConfluenceWriteRest(transport, confluence), WebhookRest(transport), self.exports)
        super().__init__(transport, raw_parts, declared)
        self._stamped = {id(p): StampedPart(p, transport) for p in raw_parts}

    def implementation_for(self, protocol: type) -> object | None:
        found = super().implementation_for(protocol)
        return None if found is None else self._stamped[id(found)]

    def gate(self, policy: AccessPolicy, op: Operation) -> str | None:
        """Writes through the UI need the explicit opt-in on top of WRITE access (and the usual confirmation)."""
        if op.access in (Access.WRITE, Access.DELETE, Access.MANAGE) and not policy.allow_browser_writes:
            return f"browser writes are not enabled; {op.id} was not routed to the browser backend (set allow_browser_writes=True to opt in)"
        return None

    @property
    def evidence(self) -> list[Evidence]:
        return self.browser_transport.evidence

    @property
    def warnings(self) -> tuple[str, ...]:
        return ("UI automation: slow, fragile to UI changes, lowest priority; a human signs in once (SSO/MFA) in the persistent profile",
                "writes are disabled unless allow_browser_writes=True and confirmed", "infinite-scroll lists report Page.total=None and may be incomplete (see evidence)")

    async def health(self) -> BackendHealth:
        try:
            snap = check_page(await self.session.open(self.browser_transport.base + "/jira/projects"))
        except Exception as exc:                                       # health must never raise
            return BackendHealth("unavailable", f"{type(exc).__name__}: {exc}")
        return BackendHealth("ready", f"signed-in profile reachable ({snap.url}); selectors v{self.browser_transport.selectors.version}")

    async def aclose(self) -> None:
        await self.session.aclose()
        await super().aclose()


class BrowserBackendFactory:
    kind: ClassVar[BackendKind] = BackendKind.BROWSER
    deployments: ClassVar[frozenset[Deployment]] = frozenset({Deployment.CLOUD, Deployment.DATA_CENTER})
    extra: ClassVar[str | None] = "browser"

    def __init__(self, session_factory: Callable[[AtlassianConfig], BrowserSession] | None = None, *, has_playwright: Callable[[], bool] | None = None) -> None:
        self._session_factory, self._has_playwright = session_factory, has_playwright

    def _playwright_ok(self) -> bool:
        if self._has_playwright is not None:
            return self._has_playwright()
        import importlib.util

        return importlib.util.find_spec("playwright") is not None

    def availability(self, config: AtlassianConfig) -> Availability:
        if self._session_factory is None and not self._playwright_ok():
            return Availability(False, "pip install 'atlian-assiant[browser]' && playwright install chromium")
        if config.browser_profile_dir is None:
            return Availability(False, "ATLASSIAN_BROWSER_PROFILE_DIR is not set: a human must sign in once in a persistent profile (SSO / MFA)")
        if PERSONAL_PROFILES.search(str(config.browser_profile_dir)):
            return Availability(False, f"{config.browser_profile_dir} looks like a personal browser profile: use a dedicated profile directory for the client")
        if not config.site:
            return Availability(False, "ATLASSIAN_SITE is not set: the browser backend needs the site to open canonical URLs")
        return Availability(True)

    def create(self, config: AtlassianConfig, core: CoreServices) -> BrowserBackend:
        avail = self.availability(config)
        if not avail.ok:
            raise BackendUnavailableError(f"browser unavailable: {avail.reason}", hint=avail.reason)
        assert config.site
        session = self._session_factory(config) if self._session_factory else PlaywrightSession(Path(str(config.browser_profile_dir)), headless=config.browser_headless)
        return BrowserBackend(session, site=config.site, allow_writes=config.allow_browser_writes)


__all__ = ["PARTIAL_OPS", "BrowserBackend", "BrowserBackendFactory", "BrowserExporter", "BrowserOpTransport", "Evidence", "Selectors"]
