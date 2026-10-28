"""DATABASE backend: factory availability, health, supported operations (design 10.12, 12.1)."""
import pytest

from atlian_assiant import AtlassianConfig, BackendKind
from atlian_assiant.backends.database import OPS, DatabaseBackend, DatabaseBackendFactory
from atlian_assiant.capabilities import Deployment
from atlian_assiant.core.errors import BackendUnavailableError, InvalidRequestError, UnsupportedOperationError
from atlian_assiant.factory import default_registry
from atlian_assiant.protocols import AnalyticsReader, JiraIssueSearcher
from atlian_assiant.testing.dbsim import SqliteExecutor

DC = dict(deployment=Deployment.DATA_CENTER, allow_direct_db=True, db_expected_product_version="9.12.4", db_url="postgresql://ro:pw@db/jira")


async def probe_9() -> str:
    return "9.12.4"


def factory(version: str = "9.12.4"):
    async def probe() -> str:
        return version
    return DatabaseBackendFactory(lambda cfg: SqliteExecutor(), lambda cfg, core: probe, has_sqlalchemy=lambda: True)


def test_registered_for_data_center_only_with_the_db_extra():
    f = default_registry().get(BackendKind.DATABASE)
    assert isinstance(f, DatabaseBackendFactory) and f.extra == "db" and f.deployments == frozenset({Deployment.DATA_CENTER})


@pytest.mark.parametrize(("cfg", "fragment"), [
    (dict(), "Data Center only; refused on Cloud"),
    (dict(deployment=Deployment.DATA_CENTER), "ALLOW_DIRECT_DB"),
    (dict(deployment=Deployment.DATA_CENTER, allow_direct_db=True), "EXPECTED_PRODUCT_VERSION"),
    (dict(deployment=Deployment.DATA_CENTER, allow_direct_db=True, db_expected_product_version="9.12.4"), "ATLASSIAN_DB_URL"),
])
def test_every_missing_opt_in_is_named(cfg, fragment):
    a = factory().availability(AtlassianConfig(**cfg))
    assert not a.ok and fragment in a.reason


def test_the_db_extra_is_required_unless_an_executor_is_injected():
    a = DatabaseBackendFactory(has_sqlalchemy=lambda: False).availability(AtlassianConfig(**DC))
    assert not a.ok and "atlian-assiant[db]" in a.reason
    assert factory().availability(AtlassianConfig(**DC)).ok


def test_create_refuses_without_the_opt_ins_and_sends_nothing():
    ex = SqliteExecutor()
    f = DatabaseBackendFactory(lambda cfg: ex, lambda cfg, core: probe_9, has_sqlalchemy=lambda: True)
    with pytest.raises(BackendUnavailableError):
        f.create(AtlassianConfig(**{**DC, "allow_direct_db": False}), core=None)
    assert ex.statements == []


def test_supported_operations_are_counting_and_the_curated_analytics_only():
    backend = factory().create(AtlassianConfig(**DC), core=None)
    assert isinstance(backend, DatabaseBackend) and backend.supported_operations() == OPS
    assert "jira.issue.search" not in OPS and "jira.issue.create" not in OPS


@pytest.mark.anyio
async def test_health_verifies_the_version_and_reports_it():
    h = await factory().create(AtlassianConfig(**DC), core=None).health()
    assert h.status == "ready" and "9.12.4" in h.detail and "3 curated" in h.detail


@pytest.mark.anyio
async def test_a_version_mismatch_makes_the_backend_unavailable_before_any_statement_runs():
    ex = SqliteExecutor()
    backend = DatabaseBackend(ex, expected_version="9.12.4", version_probe=lambda: _async("9.13.0"))
    with pytest.raises(BackendUnavailableError) as exc:
        await backend.implementation_for(JiraIssueSearcher).count_issues("project = PROJ")
    assert "9.13.0" in str(exc.value) and "db_expected_product_version" in str(exc.value) and ex.statements == []
    assert (await backend.health()).status == "unavailable"


async def _async(value: str) -> str:
    return value


@pytest.mark.anyio
async def test_a_product_version_nobody_reviewed_has_no_catalog():
    ex = SqliteExecutor()
    backend = DatabaseBackend(ex, expected_version="7.0.1", version_probe=lambda: _async("7.0.1"))
    with pytest.raises(BackendUnavailableError) as exc:
        await backend.implementation_for(AnalyticsReader).list_tables()
    assert "no curated queries" in str(exc.value) and ex.statements == []


@pytest.mark.anyio
async def test_counts_come_from_the_curated_catalog_and_search_is_not_a_database_job():
    backend = factory().create(AtlassianConfig(**DC), core=None)
    s = backend.implementation_for(JiraIssueSearcher)
    assert await s.count_issues("project = PROJ") == 3 and await s.count_issues('project = PROJ AND status = "Done"') == 1 and await s.count_issues("") == 3
    with pytest.raises(UnsupportedOperationError):
        await s.search_issues("project = PROJ")
    with pytest.raises(UnsupportedOperationError) as exc:
        await s.count_issues("assignee = currentUser()")
    assert "project = KEY" in exc.value.hint


@pytest.mark.anyio
async def test_the_analytics_reader_runs_catalog_names_only():
    backend = factory().create(AtlassianConfig(**DC), core=None)
    r = backend.implementation_for(AnalyticsReader)
    assert await r.list_shares() == ["jira-dc-database"] and "issue_count" in await r.list_tables()
    assert await r.query("issue_counts_by_project", tables=[]) == [{"project": "PROJ", "n": 3}]
    assert (await r.query("issue_count project=PROJ status=Done", tables=[]))[0]["n"] == 1
    with pytest.raises(InvalidRequestError):
        await r.query("no_such_query", tables=[])
    with pytest.raises(InvalidRequestError):
        await r.query("issue_count color=red", tables=[])
