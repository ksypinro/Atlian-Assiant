"""Executable evidence for the P5 security controls of design section 17 (SEC-07 Data Lake, SEC-08 Browser, SEC-09 Direct DB); reviewed by a human at the gate."""
import sys
import types

import pytest

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.backends.browser import BrowserBackend
from atlian_assiant.backends.browser.session import PlaywrightSession, guard_fill
from atlian_assiant.backends.data_lake import DataLakeBackend
from atlian_assiant.backends.data_lake.engine import SqliteEngine, validate_sql
from atlian_assiant.backends.data_lake.schema import GOVERNANCE_NOTICE
from atlian_assiant.backends.database import DatabaseBackend
from atlian_assiant.backends.database.executor import SqlAlchemyExecutor
from atlian_assiant.capabilities import Access, Deployment
from atlian_assiant.core.errors import (
    BackendUnavailableError,
    ConfigurationError,
    InvalidRequestError,
    NoBackendAvailableError,
    PolicyDeniedError,
)
from atlian_assiant.models.jira import IssueDraft
from atlian_assiant.policy import AccessPolicy, CallbackConfirmation
from atlian_assiant.protocols import AnalyticsReader, JiraIssueReader, JiraIssueSearcher, JiraIssueWriter
from atlian_assiant.testing import FakeBackend
from atlian_assiant.testing.browsersim import FakeBrowserSession
from atlian_assiant.testing.dbsim import SqliteExecutor
from atlian_assiant.testing.lakesim import fake_share
from atlian_assiant.testing.server import ScenarioServer

CFG = AtlassianConfig(site="example.atlassian.net")


def lake() -> DataLakeBackend:
    return DataLakeBackend(fake_share(), engine=SqliteEngine())


# ------------------------------------------------------------------------------------------------------------------------ SEC-07 Data Lake
def test_sec_07_analytics_is_in_no_default_policy():
    assert Access.ANALYTICS not in AccessPolicy().allowed and Access.ANALYTICS not in AccessPolicy.read_only().allowed and Access.ANALYTICS not in AccessPolicy.read_write().allowed
    assert Access.ANALYTICS in AccessPolicy.analytics().allowed
    assert Access.ANALYTICS not in AtlassianConfig().access


@pytest.mark.anyio
@pytest.mark.parametrize("preset", ["service", "agent", "shell", "analytics", "data_center"])
async def test_sec_07_ordinary_reads_are_never_auto_routed_to_the_lake(preset):
    only_lake = ClientFactory().build_async(CFG, backends=[], preset=preset, policy=AccessPolicy.analytics(), extra_backends={BackendKind.DATA_LAKE: lake()})
    with pytest.raises(NoBackendAvailableError):
        await only_lake.jira.issues.get("PROJ-1")
    both = ClientFactory().build_async(CFG, backends=[], preset=preset, policy=AccessPolicy.analytics(),
                                       extra_backends={BackendKind.REST: FakeBackend(), BackendKind.DATA_LAKE: lake()})
    if preset == "data_center":                                                  # that preset prefers DC backends: REST is not in its order, the lake still is not either
        with pytest.raises(NoBackendAvailableError):
            await both.jira.issues.get("PROJ-1")
    else:
        assert (await both.jira.issues.get("PROJ-1")).source.backend is BackendKind.REST


@pytest.mark.anyio
async def test_sec_07_a_forced_lake_call_is_refused_without_analytics_access_and_says_why():
    c = ClientFactory().build_async(CFG, backends=[], preset="service", policy=AccessPolicy.read_only(), extra_backends={BackendKind.DATA_LAKE: lake()})
    with pytest.raises(PolicyDeniedError) as exc:
        await c.via("data_lake").jira.issues.get("PROJ-1")
    assert "ANALYTICS" in str(exc.value) and exc.value.backend == "data_lake"
    allowed = ClientFactory().build_async(CFG, backends=[], preset="service", policy=AccessPolicy.analytics(), extra_backends={BackendKind.DATA_LAKE: lake()})
    issue = await allowed.via("data_lake").jira.issues.get("PROJ-1")
    assert issue.summary == "Login fails" and issue.source.backend is BackendKind.DATA_LAKE


@pytest.mark.anyio
async def test_sec_07_analytics_operations_are_denied_by_the_policy_before_any_backend_is_asked():
    c = ClientFactory().build_async(CFG, backends=[], preset="analytics", policy=AccessPolicy.read_only(), extra_backends={BackendKind.DATA_LAKE: lake()})
    with pytest.raises(PolicyDeniedError) as exc:
        await c.analytics.tables()
    assert "analytics access is not enabled" in str(exc.value).lower()


@pytest.mark.anyio
async def test_sec_07_every_kind_of_result_carries_the_governance_notice():
    c = ClientFactory().build_async(CFG, backends=[], preset="service", policy=AccessPolicy.analytics(), extra_backends={BackendKind.DATA_LAKE: lake()})
    via = c.via("data_lake")
    page = await via.jira.issues.search("project = PROJ")
    assert page.governance_notice == GOVERNANCE_NOTICE and page.items[0].governance_notice == GOVERNANCE_NOTICE
    assert (await via.jira.issues.get("PROJ-1")).governance_notice == GOVERNANCE_NOTICE
    assert (await via.jira.metadata.priorities()).governance_notice == GOVERNANCE_NOTICE
    assert (await c.analytics.load_table("jira_issue")).governance_notice == GOVERNANCE_NOTICE
    assert (await c.analytics.tables()).governance_notice == GOVERNANCE_NOTICE
    assert (await c.analytics.query("SELECT COUNT(*) AS n FROM jira_issue", tables=["jira_issue"])).governance_notice == GOVERNANCE_NOTICE
    text = (await c.tools.ainvoke("analytics_query", {"sql": "SELECT 1 AS one FROM jira_issue LIMIT 1", "tables": ["jira_issue"]})).text
    assert "Governance notice:" in text


def test_sec_07_the_backend_warns_in_describe_and_the_notice_says_what_not_to_do():
    c = ClientFactory().build_async(CFG, backends=[], preset="analytics", policy=AccessPolicy.analytics(), extra_backends={BackendKind.DATA_LAKE: lake()})
    notes = " ".join(c.describe()["notes"])
    assert "data_lake" in notes and "NOT row-level permissioned" in notes and "opt-in" in notes
    assert "do not show it to people" in GOVERNANCE_NOTICE


def test_sec_07_the_sql_sandbox_cannot_reach_files_extensions_or_change_data():
    for bad in ("SELECT * FROM read_csv('/etc/passwd')", "DROP TABLE jira_issue", "SELECT 1; SELECT 2", "ATTACH 'x' AS y", "COPY jira_issue TO '/tmp/x'"):
        with pytest.raises(InvalidRequestError):
            validate_sql(bad)


# ------------------------------------------------------------------------------------------------------------------------ SEC-08 Browser
CREDENTIAL_SELECTORS = ["input[type='password']", "#password", "[name='otp']", "[data-testid='mfa-code']", "#api-token", "[name=totp]", "#verification-code", ".secret"]


@pytest.mark.parametrize("selector", CREDENTIAL_SELECTORS)
def test_sec_08_credential_fields_are_never_typed_into(selector):
    with pytest.raises(PolicyDeniedError):
        guard_fill(selector, "anything")


@pytest.mark.anyio
async def test_sec_08_a_login_page_or_captcha_stops_everything_for_a_human_and_nothing_is_typed():
    from atlian_assiant.core.errors import AuthenticationError

    server = ScenarioServer()
    out = FakeBrowserSession(server, signed_in=False)
    with pytest.raises(AuthenticationError):
        await BrowserBackend(out, site="example.atlassian.net", allow_writes=True).implementation_for(JiraIssueWriter).add_comment("PROJ-1", "x")
    blocked = FakeBrowserSession(server, challenge=True)
    with pytest.raises(BackendUnavailableError):
        await BrowserBackend(blocked, site="example.atlassian.net").implementation_for(JiraIssueReader).get_issue("PROJ-1")
    for s in (out, blocked):
        assert not [c for c in s.calls if c[0] in ("fill", "click", "attach")]


@pytest.mark.anyio
async def test_sec_08_read_only_is_the_default_at_every_layer_policy_backend_and_router():
    assert AtlassianConfig().allow_browser_writes is False and AccessPolicy().allow_browser_writes is False
    server = ScenarioServer()
    session = FakeBrowserSession(server)
    backend = BrowserBackend(session, site="example.atlassian.net")                                      # allow_writes defaults to False
    with pytest.raises(PolicyDeniedError):
        await backend.implementation_for(JiraIssueWriter).add_comment("PROJ-1", "x")
    # through the router: a WRITE-capable policy still does not reach the browser unless allow_browser_writes is set
    pol = AccessPolicy(allowed=frozenset({Access.READ, Access.SEARCH, Access.WRITE}), confirm=frozenset())
    rw = BrowserBackend(FakeBrowserSession(server), site="example.atlassian.net", allow_writes=True)
    c = ClientFactory().build_async(CFG, backends=[], preset="service", policy=pol, confirm=CallbackConfirmation(lambda plan: True), extra_backends={BackendKind.BROWSER: rw})
    with pytest.raises(PolicyDeniedError) as exc:
        await c.via("browser").jira.issues.add_comment("PROJ-1", "hello")
    assert "allow_browser_writes" in str(exc.value) or "opt-in" in exc.value.hint
    opted = AccessPolicy(allowed=pol.allowed, confirm=frozenset(), allow_browser_writes=True)
    c2 = ClientFactory().build_async(CFG, backends=[], preset="service", policy=opted, confirm=CallbackConfirmation(lambda plan: True), extra_backends={BackendKind.BROWSER: rw})
    assert (await c2.via("browser").jira.issues.add_comment("PROJ-1", "hello")).body_markdown == "hello"


def test_sec_08_the_profile_directory_is_dedicated_private_and_never_a_personal_browser_profile(tmp_path):
    for personal in ("/Users/me/Library/Application Support/Google/Chrome/Default", "/home/me/.config/google-chrome", "/home/me/.mozilla/firefox/abc.default", "/home/me/.config/BraveSoftware/Brave-Browser"):
        with pytest.raises(ConfigurationError):
            PlaywrightSession(personal)
    from atlian_assiant.backends.browser import BrowserBackendFactory

    assert not BrowserBackendFactory(has_playwright=lambda: True).availability(AtlassianConfig(site="x.atlassian.net", browser_profile_dir="/home/me/.config/google-chrome")).ok
    launched = {}

    class Ctx:
        pages: list = []

        def set_default_timeout(self, ms):
            pass

        async def new_page(self):
            return types.SimpleNamespace(url="about:blank")

    class Chromium:
        async def launch_persistent_context(self, profile, **kw):
            launched["profile"] = profile
            return Ctx()

    pw = types.SimpleNamespace(chromium=Chromium())
    module = types.SimpleNamespace(async_playwright=lambda: types.SimpleNamespace(start=lambda: _return(pw)))
    import anyio

    anyio.run(PlaywrightSession(tmp_path / "profile", playwright_module=module)._ensure)
    assert oct((tmp_path / "profile").stat().st_mode & 0o777) == "0o700" and launched["profile"] == str(tmp_path / "profile")


async def _return(value):
    return value


# ------------------------------------------------------------------------------------------------------------------------ SEC-09 Direct DB
async def _v(version: str) -> str:
    return version


def db(version: str = "9.12.4", expected: str = "9.12.4"):
    ex = SqliteExecutor()
    return DatabaseBackend(ex, expected_version=expected, version_probe=lambda: _v(version)), ex


def test_sec_09_opt_in_cloud_refusal_and_every_prerequisite():
    from atlian_assiant.backends.database import DatabaseBackendFactory

    f = DatabaseBackendFactory(lambda cfg: SqliteExecutor(), lambda cfg, core: lambda: _v("9.12.4"), has_sqlalchemy=lambda: True)
    base = dict(deployment=Deployment.DATA_CENTER, allow_direct_db=True, db_expected_product_version="9.12.4", db_url="postgresql://ro@db/jira")
    assert f.availability(AtlassianConfig(**base)).ok
    for missing in ("allow_direct_db", "db_expected_product_version", "db_url"):
        cfg = {k: v for k, v in base.items() if k != missing}
        assert not f.availability(AtlassianConfig(**cfg)).ok, missing
    assert "Cloud" in f.availability(AtlassianConfig(**{**base, "deployment": Deployment.CLOUD})).reason
    assert AtlassianConfig().allow_direct_db is False and AccessPolicy().allow_direct_db is False


@pytest.mark.anyio
async def test_sec_09_a_pinned_version_mismatch_sends_nothing_to_the_database():
    backend, ex = db(version="9.13.0")
    with pytest.raises(BackendUnavailableError):
        await backend.implementation_for(JiraIssueSearcher).count_issues("project = PROJ")
    with pytest.raises(BackendUnavailableError):
        await backend.implementation_for(AnalyticsReader).query("issue_count", tables=[])
    assert ex.statements == []


@pytest.mark.anyio
async def test_sec_09_only_curated_parameterized_statements_ever_run_and_values_are_bound_never_interpolated():
    backend, ex = db()
    hostile = "Done'; DROP TABLE jiraissue; --"
    r = backend.implementation_for(AnalyticsReader)
    assert (await r.query(f'issue_count status="{hostile}"', tables=[])) == [{"n": 0}]
    from atlian_assiant.backends.database.catalog import JIRA_DC_QUERIES

    sql, params = ex.statements[-1]
    assert sql in {q.sql for q in JIRA_DC_QUERIES.values()} and params["status"] == hostile and hostile not in sql
    assert ex.db.execute("SELECT COUNT(*) FROM jiraissue").fetchone()[0] == 3                  # the table is intact
    for free_form in ("SELECT * FROM jiraissue", "DELETE FROM jiraissue", "drop table jiraissue"):
        with pytest.raises(PolicyDeniedError):
            await r.query(free_form, tables=[])
    assert all(s in {q.sql for q in JIRA_DC_QUERIES.values()} for s, _ in ex.statements)


@pytest.mark.anyio
async def test_sec_09_every_catalog_statement_is_a_single_select_and_the_executor_cannot_write():
    from atlian_assiant.backends.database.catalog import CATALOGS

    for catalog in CATALOGS.values():
        for q in catalog.values():
            validate_sql(q.sql)                                                                  # one SELECT, nothing else
            assert ":" in q.sql or not q.params
    ex = SqliteExecutor()
    with pytest.raises(Exception, match="readonly"):
        await ex.fetch("INSERT INTO project VALUES (99, 'X', 'x')", {})


@pytest.mark.anyio
async def test_sec_09_the_sqlalchemy_executor_opens_a_read_only_transaction_before_every_statement(monkeypatch):
    log: list[str] = []

    class Conn:
        def exec_driver_sql(self, sql):
            log.append(f"driver:{sql}")

        def execute(self, stmt, params):
            log.append(f"execute:{stmt}")
            return types.SimpleNamespace(mappings=lambda: types.SimpleNamespace(all=lambda: [{"n": 1}]))

        def rollback(self):
            log.append("rollback")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def make_engine(dialect):
        return types.SimpleNamespace(dialect=types.SimpleNamespace(name=dialect), connect=lambda: Conn(), dispose=lambda: None)

    fake = types.ModuleType("sqlalchemy")
    fake.create_engine = lambda url: make_engine("postgresql" if url.startswith("postgres") else url.split(":")[0])   # type: ignore[attr-defined]
    fake.text = lambda sql: sql                                                                                      # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sqlalchemy", fake)
    ex = SqlAlchemyExecutor("postgresql://ro@db/jira")
    assert await ex.fetch("SELECT 1 AS n", {}) == [{"n": 1}]
    assert log[0] == "driver:SET TRANSACTION READ ONLY" and log[1].startswith("execute:") and log[-1] == "rollback"
    with pytest.raises(BackendUnavailableError):
        SqlAlchemyExecutor("sqlite://x")                                                                              # a dialect without a read-only statement is refused


@pytest.mark.anyio
async def test_sec_09_the_database_is_not_routed_to_unless_direct_db_is_allowed():
    backend, ex = db()
    pol = AccessPolicy(allowed=frozenset({Access.READ, Access.SEARCH}))
    denied = ClientFactory().build_async(CFG, backends=[], preset="data_center", policy=pol, extra_backends={BackendKind.DATABASE: backend})
    with pytest.raises(PolicyDeniedError):
        await denied.via("database").jira.issues.count("project = PROJ")
    with pytest.raises(NoBackendAvailableError):
        await denied.jira.issues.count("project = PROJ")                                                               # skipped silently when not forced
    assert ex.statements == []
    opted = AccessPolicy(allowed=pol.allowed, allow_direct_db=True)
    ok = ClientFactory().build_async(CFG, backends=[], preset="data_center", policy=opted, extra_backends={BackendKind.DATABASE: backend})
    assert await ok.jira.issues.count("project = PROJ") == 3 and len(ex.statements) == 1
    assert IssueDraft and not [s for s, _ in ex.statements if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
