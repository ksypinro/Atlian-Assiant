"""P5.BROWSER.001: Playwright session, versioned selectors.yaml, native exports, evidence screenshots (design 10.8; test layer 6)."""
from __future__ import annotations

import re
from pathlib import Path

import anyio
import pytest

from atlian_assiant.backends.browser import PARTIAL_OPS, BrowserBackend
from atlian_assiant.backends.browser.dom import parse
from atlian_assiant.backends.browser.extract import Selectors, extract_object, extract_rows, parse_field
from atlian_assiant.backends.browser.ops import BINDINGS, WRITE_OPS
from atlian_assiant.backends.browser.session import PlaywrightSession, Snapshot, check_page, guard_fill
from atlian_assiant.capabilities import BackendKind
from atlian_assiant.core.errors import (
    AuthenticationError,
    BackendUnavailableError,
    NotFoundError,
    PolicyDeniedError,
    SelectorDriftError,
    UnsupportedOperationError,
)
from atlian_assiant.matrix import declared_operations
from atlian_assiant.models.jira import IssueUpdate
from atlian_assiant.protocols import (
    ConfluenceContentReader,
    ConfluenceExporter,
    JiraExporter,
    JiraIssueReader,
    JiraIssueSearcher,
    JiraIssueWriter,
)
from atlian_assiant.testing.browsersim import FakeBrowserSession, render_page
from atlian_assiant.testing.server import ScenarioServer

pytestmark = [pytest.mark.layer_06, pytest.mark.anyio]


def make(*, writes: bool = False, **session_kw):
    server = ScenarioServer()
    session = FakeBrowserSession(server, **session_kw)
    return BrowserBackend(session, site="example.atlassian.net", allow_writes=writes), session, server


# ---------------------------------------------------------------------------------------------------------------- selector engine
def test_dom_selector_engine_supports_the_forms_selectors_yaml_uses():
    root = parse("<div id=a class='x y'><ul><li data-id='1'><span data-testid='k'>Hi <b>there</b></span></li><li data-id='2'></li></ul></div><p>z</p>")
    assert [n.attr("data-id") for n in root.select("li")] == ["1", "2"]
    assert root.select("[data-testid='k']")[0].text() == "Hi there"
    assert len(root.select("div.x > ul > li")) == 2 and len(root.select("#a li, p")) == 3 and root.select("div p") == []
    assert root.select("[data-testid='k']")[0].inner_html() == "Hi <b>there</b>"
    with pytest.raises(ValueError):
        root.select("li:nth-child(2)")


def test_field_specs_parse_types_lists_optionals_and_nested_rows():
    assert parse_field("count:int").type == "int" and parse_field("labels[]").is_list and parse_field("x.y?").optional
    nested = parse_field({"items[]": {"rows": "[data-testid='i']", "fields": ["a", "b?"]}})
    assert nested.is_list and nested.rows == ("[data-testid='i']",) and [f.path for f in nested.fields] == ["a", "b"]
    with pytest.raises(ValueError):
        parse_field("not a field!")


def test_selectors_yaml_is_versioned_complete_and_never_mentions_private_endpoints():
    sel = Selectors.load()
    assert sel.version >= 1 and len(sel.pages) >= 45
    for name, page in sel.pages.items():
        assert page.fields, name
        assert page.ready or page.rows, name
    used = {p for b in BINDINGS for p in (getattr(b, "page", None), getattr(b, "result_page", None)) if p}
    assert used <= set(sel.pages)
    text = Path("atlian_assiant/backends/browser/selectors.yaml").read_text()
    assert "/rest/" not in text and "graphql" not in text.lower()


def test_every_binding_opens_a_canonical_ui_url_never_a_private_xhr_or_graphql_endpoint():
    for b in BINDINGS:
        assert not re.search(r"/rest/|graphql|/gateway/|/_edge/|\.json\b|/rpc", b.url), f"{b.op}: {b.url}"


# ---------------------------------------------------------------------------------------------------------------- extraction + drift
async def test_object_and_row_extraction_round_trip_through_the_generated_fixtures():
    sel = Selectors.load()
    issue = {"id": "10001", "key": "PROJ-1", "fields": {"summary": "S", "status": {"name": "Done", "statusCategory": {"name": "Done"}}, "project": {"key": "P"}, "labels": ["a", "b"]}}
    got = extract_object(render_page(sel.page("issue.view"), issue), sel.page("issue.view"))
    assert got["key"] == "PROJ-1" and got["fields"]["labels"] == ["a", "b"] and got["fields"]["status"]["statusCategory"]["name"] == "Done"
    rows = [{"number": 3, "message": "tweak", "createdAt": "2026-09-01"}, {"number": 2}]
    out = extract_rows(render_page(sel.page("conf.versions"), rows), sel.page("conf.versions"))
    assert out == [{"number": 3, "message": "tweak", "createdAt": "2026-09-01"}, {"number": 2}]
    nested = [{"id": "h1", "items": [{"field": "status", "fromString": "To Do", "toString": "Done"}]}]
    assert extract_rows(render_page(sel.page("issue.history"), nested), sel.page("issue.history"))[0]["items"] == [{"field": "status", "fromString": "To Do", "toString": "Done"}]


async def test_a_fallback_selector_keeps_working_when_the_primary_one_drifts():
    backend, session, _ = make()
    session.use_legacy_markup("issue.key", "id=\"key-val\"")
    issue = await backend.implementation_for(JiraIssueReader).get_issue("PROJ-1")
    assert issue.key == "PROJ-1"


async def test_selector_drift_is_an_explicit_error_never_a_guess():
    backend, session, _ = make()
    session.rename_testids({"issue.summary": "issue.title"})
    with pytest.raises(SelectorDriftError) as exc:
        await backend.implementation_for(JiraIssueReader).get_issue("PROJ-1")
    assert "fields.summary" in str(exc.value) and "selectors.yaml" in exc.value.hint
    backend2, session2, _ = make()
    session2.rename_testids({"issue.view": "issue.panel"})
    with pytest.raises(SelectorDriftError) as exc2:
        await backend2.implementation_for(JiraIssueReader).get_issue("PROJ-1")
    assert "container" in str(exc2.value)


# ---------------------------------------------------------------------------------------------------------------- human-only sign-in
async def test_a_signed_out_profile_asks_a_human_to_sign_in_and_nothing_is_typed():
    backend, session, _ = make(signed_in=False, writes=True)
    with pytest.raises(AuthenticationError) as exc:
        await backend.implementation_for(JiraIssueReader).get_issue("PROJ-1")
    assert "human" in exc.value.hint and "never types credentials" in exc.value.hint
    with pytest.raises(AuthenticationError):
        await backend.implementation_for(JiraIssueWriter).add_comment("PROJ-1", "hi")
    assert not [c for c in session.calls if c[0] in ("fill", "click", "attach")]


async def test_a_captcha_is_reported_for_a_human_and_never_solved():
    backend, session, _ = make(challenge=True)
    with pytest.raises(BackendUnavailableError) as exc:
        await backend.implementation_for(JiraIssueReader).get_issue("PROJ-1")
    assert "CAPTCHA" in str(exc.value) and "never solves" in exc.value.hint
    assert [c[0] for c in session.calls] == ["open"]


def test_login_and_challenge_detection_on_raw_snapshots():
    with pytest.raises(AuthenticationError):
        check_page(Snapshot("https://id.atlassian.com/login?continue=x", 200, "<html></html>"))
    with pytest.raises(AuthenticationError):
        check_page(Snapshot("https://example.atlassian.net/browse/X", 200, "<input type='password'>"))
    with pytest.raises(BackendUnavailableError):
        check_page(Snapshot("https://example.atlassian.net/x", 200, "<div class='cf-turnstile'></div>"))
    assert check_page(Snapshot("https://example.atlassian.net/browse/X", 200, "<html>ok</html>")).status == 200


@pytest.mark.parametrize("selector", ["input[type='password']", "#password", "[name='otp']", "[data-testid='mfa-code']", "input#api-token", "[name=totp]", "#verification-code", ".secret"])
def test_credential_looking_fields_are_never_typed_into(selector):
    with pytest.raises(PolicyDeniedError) as exc:
        guard_fill(selector, "anything")
    assert "never enters passwords" in exc.value.hint


def test_ordinary_form_fields_are_allowed():
    guard_fill("[data-testid='comment.input']", "hello")
    guard_fill("[data-testid='summary.input']", "a title")


async def test_the_playwright_session_refuses_credential_fields_before_touching_the_page(tmp_path):
    class Page:
        url = "https://example.atlassian.net"
        filled: list[str] = []

        async def fill(self, selector, value):
            self.filled.append(selector)

    session = PlaywrightSession(tmp_path / "profile", playwright_module=object())
    session._page = Page()
    with pytest.raises(PolicyDeniedError):
        await session.fill("input[type=password]", "x")
    await session.fill("[data-testid='comment.input']", "ok")
    assert Page.filled == ["[data-testid='comment.input']"]


# ---------------------------------------------------------------------------------------------------------------- read-only default, writes opt-in
async def test_writes_are_not_declared_and_are_refused_without_the_opt_in():
    backend, session, _ = make(writes=False)
    assert not (backend.supported_operations() & WRITE_OPS)
    with pytest.raises(PolicyDeniedError) as exc:
        await backend.implementation_for(JiraIssueWriter).add_comment("PROJ-1", "hello")
    assert "allow_browser_writes" in exc.value.hint
    assert not [c for c in session.calls if c[0] in ("open", "fill", "click", "attach")]


async def test_with_the_opt_in_the_ui_flows_run_and_the_result_carries_the_new_item():
    backend, session, server = make(writes=True)
    assert WRITE_OPS <= backend.supported_operations()
    w = backend.implementation_for(JiraIssueWriter)
    c = await w.add_comment("PROJ-1", "from the browser")
    assert c.body_markdown == "from the browser" and c.id
    await w.transition_issue("PROJ-1", "Done")
    await w.edit_issue("PROJ-1", IssueUpdate(summary="Renamed by UI"))
    assert [x[0] for x in session.calls if x[0] in ("fill", "click")] == ["fill", "click", "click", "fill", "click"]
    assert any(r["method"] == "PUT" and r["path"].endswith("/issue/PROJ-1") for r in server.requests)
    assert not any(c[0] == "fill" and "password" in c[1] for c in session.calls)


async def test_flows_refuse_payloads_the_ui_cannot_express():
    backend, session, _ = make(writes=True)
    with pytest.raises(UnsupportedOperationError):
        await backend.implementation_for(JiraIssueWriter).edit_issue("PROJ-1", IssueUpdate(summary="x", labels_add=["y"]))
    assert not [c for c in session.calls if c[0] in ("fill", "click")]


# ---------------------------------------------------------------------------------------------------------------- evidence, totals, completeness
async def test_results_carry_source_url_and_screenshot_evidence(tmp_path):
    server = ScenarioServer()
    session = FakeBrowserSession(server)
    backend = BrowserBackend(session, site="example.atlassian.net", screenshot_dir=tmp_path / "shots")
    issue = await backend.implementation_for(JiraIssueReader).get_issue("PROJ-1")
    assert issue.source and issue.source.backend is BackendKind.BROWSER and issue.source.url == "https://example.atlassian.net/browse/PROJ-1"
    shots = sorted((tmp_path / "shots").glob("*.png"))
    assert len(shots) == 1 and shots[0].name.endswith("jira.issue.get.png") and backend.evidence[0].screenshot == shots[0]


async def test_scrolled_lists_report_no_total_and_flag_possible_incompleteness():
    backend, session, _ = make()
    backend.browser_transport.max_rows = 2
    page = await backend.implementation_for(JiraIssueSearcher).search_issues("project = PROJ", limit=50)
    assert page.total is None and len(page.items) == 2 and page.source and page.source.url.startswith("https://example.atlassian.net/issues/")
    assert backend.evidence[-1].incomplete is True
    backend2, _, _ = make()
    assert (await backend2.implementation_for(JiraIssueSearcher).search_issues("project = PROJ")).total is None
    assert backend2.evidence[-1].incomplete is False


async def test_a_missing_page_is_not_found_with_the_permission_hint():
    backend, _, _ = make()
    with pytest.raises(NotFoundError) as exc:
        await backend.implementation_for(JiraIssueReader).get_issue("PROJ-999")
    assert "permission" in exc.value.hint.lower()


async def test_historic_confluence_versions_are_refused_because_the_page_cannot_show_them():
    backend, _, _ = make()
    with pytest.raises(UnsupportedOperationError):
        await backend.implementation_for(ConfluenceContentReader).get_content("123", version=2)


# ---------------------------------------------------------------------------------------------------------------- native exports
async def test_native_exports_download_through_the_signed_in_session(tmp_path):
    backend, session, _ = make()
    csv_path = await backend.implementation_for(JiraExporter).export_issues_csv("project = PROJ", dest=tmp_path / "i.csv")
    assert csv_path.read_text().splitlines()[0] == "key,summary,status"
    ex = backend.implementation_for(ConfluenceExporter)
    assert (await ex.export_content("123", format="pdf", dest=tmp_path / "p.pdf")).read_bytes().startswith(b"%PDF")
    assert (await ex.export_content("123", format="word", dest=tmp_path / "p.doc")).exists()
    assert "<h1>Runbook</h1>" in (await ex.export_content("123", format="html", dest=tmp_path / "p.html")).read_text()
    assert b"space-export:ENG:xml" == (await ex.export_space("ENG", format="xml", dest=tmp_path / "s.zip")).read_bytes()
    with pytest.raises(UnsupportedOperationError) as md:
        await ex.export_content("123", format="markdown", dest=tmp_path / "p.md")
    assert "REST synthesizes" in md.value.hint
    with pytest.raises(UnsupportedOperationError):
        await ex.export_space("ENG", format="csv", dest=tmp_path / "s.csv")
    downloads = [c[1] for c in session.calls if c[0] == "download"]
    assert downloads and all(u.startswith("https://example.atlassian.net/") for u in downloads)
    assert not any("/rest/" in u or "graphql" in u for c in session.calls if c[0] in ("open", "download") for u in [c[1]])


# ---------------------------------------------------------------------------------------------------------------- persistent profile / concurrency
class StubPage:
    def __init__(self):
        self.url = "about:blank"
        self.active = 0
        self.max_active = 0

    async def goto(self, url, wait_until=None):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await anyio.sleep(0.01)
        self.active -= 1
        self.url = url
        return type("R", (), {"status": 200})()

    async def content(self):
        return "<html>ok</html>"


def stub_playwright(page):
    launched = {}

    class Context:
        pages = [page]

        def set_default_timeout(self, ms):
            launched["timeout"] = ms

        async def close(self):
            launched["closed"] = True

    class Chromium:
        async def launch_persistent_context(self, profile, **kw):
            launched.update(profile=profile, **kw)
            return Context()

    class PW:
        chromium = Chromium()

        async def stop(self):
            launched["stopped"] = True

    class Starter:
        async def start(self):
            return PW()

    module = type("M", (), {"async_playwright": staticmethod(lambda: Starter())})
    return module, launched


async def test_the_session_uses_a_persistent_profile_and_serializes_page_access(tmp_path):
    page = StubPage()
    module, launched = stub_playwright(page)
    session = PlaywrightSession(tmp_path / "profile", headless=False, timeout_ms=5000, playwright_module=module)
    async with anyio.create_task_group() as tg:
        for i in range(4):
            tg.start_soon(session.open, f"https://example.atlassian.net/browse/PROJ-{i}")
    assert page.max_active == 1                                          # concurrency 1
    assert launched["profile"] == str(tmp_path / "profile") and launched["headless"] is False and launched["accept_downloads"] is True and launched["timeout"] == 5000
    assert (tmp_path / "profile").is_dir()
    await session.aclose()
    assert launched["closed"] and launched["stopped"]


async def test_a_missing_playwright_install_says_how_to_fix_it(tmp_path, monkeypatch):
    import importlib

    real = importlib.import_module
    monkeypatch.setattr(importlib, "import_module", lambda name, *a, **k: (_ for _ in ()).throw(ImportError(name)) if name.startswith("playwright") else real(name, *a, **k))
    with pytest.raises(BackendUnavailableError) as exc:
        await PlaywrightSession(tmp_path / "p").open("https://example.atlassian.net")
    assert "atlian-assiant[browser]" in exc.value.hint and "playwright install" in exc.value.hint


def test_the_browser_backend_declares_exactly_its_matrix_cells_and_is_the_lowest_priority():
    from atlian_assiant.routing import PRESET_ORDERS

    backend, _, _ = make(writes=True)
    assert backend.supported_operations() == declared_operations(BackendKind.BROWSER, PARTIAL_OPS)
    for preset, orders in PRESET_ORDERS.items():
        for order in orders.values():
            if BackendKind.BROWSER in order:
                assert order[-1] is BackendKind.BROWSER or preset.value == "data_center", (preset, order)
