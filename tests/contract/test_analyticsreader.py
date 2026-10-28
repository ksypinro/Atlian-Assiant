"""Contract suite for AnalyticsReader (design 9.6, 10.9, 10.12): Fake, Data Lake and the curated database backend."""
import pytest

from atlian_assiant import BackendKind
from atlian_assiant.backends.database import DatabaseBackend
from atlian_assiant.core.errors import InvalidRequestError, NotFoundError, PolicyDeniedError
from atlian_assiant.protocols import AnalyticsReader
from atlian_assiant.protocols.base import implementation_of
from atlian_assiant.testing import FakeBackend
from atlian_assiant.testing.dbsim import SqliteExecutor

from .matrix_backends import lake_ctx

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]


async def _version() -> str:
    return "9.12.4"


@pytest.fixture(params=["fake", "data_lake", "database"])
def case(request):
    """(reader, a read query, tables for it, a table known to exist)"""
    if request.param == "fake":
        return implementation_of(FakeBackend(BackendKind.DATA_LAKE), AnalyticsReader), "SELECT COUNT(*) AS n FROM jira_issue", ["jira_issue"], "jira_issue"
    if request.param == "data_lake":
        ctx = lake_ctx()
        return ctx.impl(AnalyticsReader), "SELECT status, COUNT(*) AS n FROM jira_issue GROUP BY status", ["jira_issue"], "jira_issue"
    backend = DatabaseBackend(SqliteExecutor(), expected_version="9.12.4", version_probe=_version)
    return implementation_of(backend, AnalyticsReader), "issue_counts_by_status", [], "issue_counts_by_status"


async def test_shares_and_tables_are_listed_as_strings(case):
    reader, _, _, table = case
    shares, tables = await reader.list_shares(), await reader.list_tables()
    assert shares and all(isinstance(s, str) for s in shares)
    assert any(t == table or t.endswith("." + table) for t in tables)


async def test_load_table_returns_records_and_honours_limit_and_columns(case):
    reader, _, _, table = case
    rows = await reader.load_table(table)
    assert rows and all(isinstance(r, dict) for r in rows)
    assert len(await reader.load_table(table, limit=1)) == 1
    first = next(iter(rows[0]))
    assert list((await reader.load_table(table, columns=[first], limit=1))[0]) == [first]


async def test_an_unknown_table_is_not_found_with_a_hint(case):
    reader, *_ = case
    with pytest.raises(NotFoundError) as exc:
        await reader.load_table("no_such_table")
    assert exc.value.hint


async def test_query_returns_records(case):
    reader, sql, tables, _ = case
    rows = await reader.query(sql, tables=tables)
    assert rows and all(isinstance(r, dict) for r in rows)


@pytest.mark.parametrize("sql", ["DROP TABLE jira_issue", "SELECT 1; DELETE FROM jira_issue", "UPDATE jira_issue SET status = 'x'", "INSERT INTO jira_issue VALUES (1)"])
async def test_query_refuses_anything_that_is_not_one_read_only_statement(case, sql):
    reader, _, tables, _ = case
    with pytest.raises((InvalidRequestError, PolicyDeniedError)):
        await reader.query(sql, tables=tables or ["jira_issue"])


def test_the_reference_the_lake_and_the_database_implement_the_same_protocol():
    parts = (implementation_of(FakeBackend(BackendKind.DATA_LAKE), AnalyticsReader), lake_ctx().backend.analytics,
             DatabaseBackend(SqliteExecutor(), expected_version="9.12.4", version_probe=_version).analytics)
    assert all(isinstance(p, AnalyticsReader) for p in parts)
