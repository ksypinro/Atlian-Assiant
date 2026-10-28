"""DATA_LAKE backend: factory availability, health, supported operations, SQL sandbox (design 10.9, 12.1)."""
import pytest

from atlian_assiant import AtlassianConfig, BackendKind
from atlian_assiant.backends.data_lake import ANALYTICS_OPS, PARTIAL_OPS, DataLakeBackend, DataLakeBackendFactory
from atlian_assiant.backends.data_lake.engine import DuckDbEngine, SqliteEngine, make_engine, validate_sql
from atlian_assiant.backends.data_lake.schema import TableMap
from atlian_assiant.backends.data_lake.share import DeltaSharingClient
from atlian_assiant.capabilities import Access, Deployment
from atlian_assiant.core.errors import (
    BackendUnavailableError,
    InvalidRequestError,
    NotFoundError,
    UnsupportedOperationError,
)
from atlian_assiant.factory import default_registry
from atlian_assiant.matrix import declared_operations
from atlian_assiant.protocols import AnalyticsReader, ConfluenceContentReader, JiraIssueReader, JiraIssueSearcher
from atlian_assiant.testing.lakesim import fake_share, lake_tables

from ..contract.matrix_backends import lake_ctx


def factory():
    return DataLakeBackendFactory(lambda cfg: fake_share())


def test_registered_for_cloud_with_the_datalake_extra():
    f = default_registry().get(BackendKind.DATA_LAKE)
    assert isinstance(f, DataLakeBackendFactory) and f.extra == "datalake" and f.deployments == frozenset({Deployment.CLOUD})


def test_availability_names_the_missing_piece(tmp_path):
    assert "atlian-assiant[datalake]" in DataLakeBackendFactory(has_delta_sharing=lambda: False).availability(AtlassianConfig()).reason
    f = DataLakeBackendFactory(has_delta_sharing=lambda: True)
    assert "DELTA_SHARING_PROFILE" in f.availability(AtlassianConfig()).reason
    assert "does not exist" in f.availability(AtlassianConfig(delta_sharing_profile=tmp_path / "nope.json")).reason
    profile = tmp_path / "share.json"
    profile.write_text("{}")
    assert f.availability(AtlassianConfig(delta_sharing_profile=profile)).ok


def test_create_without_prerequisites_raises_backend_unavailable_and_with_them_builds(tmp_path):
    with pytest.raises(BackendUnavailableError):
        DataLakeBackendFactory(has_delta_sharing=lambda: False).create(AtlassianConfig(), core=None)
    backend = factory().create(AtlassianConfig(delta_sharing_profile=tmp_path / "p.json"), core=None)
    assert isinstance(backend, DataLakeBackend) and backend.kind is BackendKind.DATA_LAKE


def test_supported_operations_are_the_declared_cells_plus_the_analytics_and_export_operations():
    backend = lake_ctx().backend
    assert backend.supported_operations() == declared_operations(BackendKind.DATA_LAKE, PARTIAL_OPS)
    assert ANALYTICS_OPS <= backend.supported_operations() and "export.jira.csv" in backend.supported_operations()
    assert "jira.issue.create" not in backend.supported_operations() and "jira.issue.attachment.download" not in backend.supported_operations()


@pytest.mark.anyio
async def test_health_lists_shares_and_never_raises():
    ok = await lake_ctx().backend.health()
    assert ok.status == "ready" and "ANALYTICS" in ok.detail

    class Broken:
        def list_shares(self):
            raise RuntimeError("profile expired")

    assert (await DataLakeBackend(Broken(), engine=SqliteEngine()).health()).status == "unavailable"


def test_the_sql_sandbox_allows_one_select_and_nothing_that_touches_files_or_changes_data():
    assert validate_sql("SELECT a FROM t WHERE b = 'DROP TABLE x; -- not a statement'") and validate_sql("WITH x AS (SELECT 1) SELECT * FROM x;")
    for bad in ["SELECT 1; SELECT 2", "DROP TABLE t", "DELETE FROM t", "SELECT * FROM read_csv('/etc/passwd')", "SELECT readfile('/etc/passwd')", "PRAGMA table_info(t)",
                "ATTACH 'x.db' AS y", "COPY t TO '/tmp/x'", "INSERT INTO t SELECT 1", "SELECT load_extension('x')", "CREATE TABLE t AS SELECT 1", ""]:
        with pytest.raises(InvalidRequestError):
            validate_sql(bad)


def test_engines_hold_text_so_ids_compare_the_same_way_and_the_default_is_sqlite_without_duckdb():
    e = SqliteEngine()
    e.load("t", ("id", "ok"), [{"id": 42, "ok": True}])
    assert e.query("SELECT * FROM t WHERE id = ?", ["42"]) == [{"id": "42", "ok": "true"}]
    with pytest.raises(InvalidRequestError):
        e.query("SELECT * FROM missing")
    with pytest.raises(InvalidRequestError):
        e.load("bad name", ("id",), [])
    try:
        import duckdb  # noqa: F401
    except ImportError:
        assert isinstance(make_engine(), SqliteEngine)
        with pytest.raises(BackendUnavailableError) as exc:
            make_engine("duckdb")
        assert "atlian-assiant[duckdb]" in exc.value.hint
    else:                                                                                              # pragma: no cover - only where the extra is installed
        assert isinstance(make_engine(), DuckDbEngine)


@pytest.mark.anyio
async def test_query_only_sees_the_tables_it_names_and_cannot_change_the_lake():
    reader = lake_ctx().impl(AnalyticsReader)
    assert await reader.query("SELECT COUNT(*) AS n FROM jira_issue", tables=["jira_issue"]) == [{"n": 3}]
    with pytest.raises(InvalidRequestError):
        await reader.query("SELECT * FROM jira_comment", tables=["jira_issue"])          # a table that was not named does not exist in the sandbox
    with pytest.raises(InvalidRequestError):
        await reader.query("SELECT 1", tables=[])
    assert len(await reader.load_table("jira_issue")) == 3


@pytest.mark.anyio
async def test_search_uses_a_jql_subset_and_refuses_the_rest_so_the_router_falls_back():
    s = lake_ctx().impl(JiraIssueSearcher)
    assert [i.key for i in (await s.search_issues("project = PROJ AND status = Done")).items] == ["PROJ-3"]
    assert [i.key for i in (await s.search_issues('assignee = "Ann Lee" ORDER BY key DESC')).items] == ["PROJ-1"]
    assert await s.count_issues("labels = backend") == 1
    for jql in ("project = PROJ OR project = OPS", "assignee = currentUser()", "updated >= -7d", "foo = bar", "project in (A, B) AND status changed"):
        with pytest.raises(UnsupportedOperationError) as exc:
            await s.search_issues(jql)
        assert "subset" in exc.value.hint


@pytest.mark.anyio
async def test_missing_rows_are_not_found_and_a_remapped_schema_is_one_table_map():
    ctx = lake_ctx()
    with pytest.raises(NotFoundError):
        await ctx.impl(JiraIssueReader).get_issue("PROJ-404")
    with pytest.raises(NotFoundError):
        await ctx.impl(ConfluenceContentReader).get_content("999")
    from atlian_assiant.backends.data_lake.engine import SqliteEngine as Eng
    from atlian_assiant.backends.data_lake.share import InMemoryShareClient

    renamed = InMemoryShareClient({"JIRA_ISSUES_V2": lake_tables()["jira_issue"]}, share="corp")
    backend = DataLakeBackend(renamed, engine=Eng(), tables=TableMap({"jira_issue": "JIRA_ISSUES_V2"}))
    assert (await backend.implementation_for(JiraIssueReader).get_issue("PROJ-1")).summary == "Login fails"


@pytest.mark.anyio
async def test_tables_are_loaded_once_per_backend_until_refreshed():
    ctx = lake_ctx()
    s = ctx.impl(JiraIssueReader)
    await s.get_issue("PROJ-1")
    await s.get_issue("PROJ-2")
    assert ctx.fake.loads.count("atlassian.default.jira_issue") == 1
    ctx.backend.lake.refresh()
    await s.get_issue("PROJ-1")
    assert ctx.fake.loads.count("atlassian.default.jira_issue") == 2


def test_the_real_delta_sharing_client_reports_how_to_install_or_configure_it(tmp_path):
    with pytest.raises(BackendUnavailableError) as exc:
        DeltaSharingClient(tmp_path / "missing.json").list_shares()
    assert "datalake" in exc.value.hint or "PROFILE" in exc.value.hint
    assert Access.ANALYTICS.value == "analytics"
