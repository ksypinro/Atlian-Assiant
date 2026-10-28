"""DATABASE backend: Data Center direct database, read-only reporting only (design 10.12). Opt-in, discouraged (DA 24).

Refuses Cloud. Needs ``allow_direct_db=True`` AND ``db_expected_product_version``, which must match the product's own serverInfo; otherwise ``BackendUnavailableError``
and NOTHING is sent to the database. Only curated, parameterized, read-only statements run (``catalog.py``): no free-form SQL from agents, never a write.
"""
from __future__ import annotations

import re
import shlex
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, ClassVar, Literal

from ...capabilities import BackendKind, Deployment, Operation
from ...config import AtlassianConfig
from ...core.errors import (
    BackendUnavailableError,
    InvalidRequestError,
    NotFoundError,
    PolicyDeniedError,
    UnsupportedOperationError,
)
from ...core.services import CoreServices
from ...factory import Availability
from ...models.common import Page
from ...models.jira import Issue
from ...policy import AccessPolicy
from ...protocols.base import BackendHealth
from .catalog import CuratedQuery, catalog_for
from .executor import DbExecutor, SqlAlchemyExecutor

OPS = frozenset({"jira.issue.count", "analytics.share.list", "analytics.table.list", "analytics.table.load", "analytics.table.query"})
_PROJECT = re.compile(r"""^\s*project\s*=\s*["']?([A-Za-z][A-Za-z0-9_]*)["']?\s*(?:and\s+status\s*=\s*["']?([^"']+?)["']?\s*)?$""", re.I)


class DbGate:
    """Everything that must hold before ONE statement is allowed to reach the database. Verified lazily and cached; failure sends nothing."""

    def __init__(self, executor: DbExecutor, expected_version: str, version_probe: Callable[[], Awaitable[str]]) -> None:
        self.executor, self.expected, self._probe = executor, expected_version, version_probe
        self._catalog: dict[str, CuratedQuery] | None = None
        self.verified_version: str | None = None

    async def catalog(self) -> dict[str, CuratedQuery]:
        if self._catalog is None:
            actual = await self._probe()
            if actual != self.expected:
                raise BackendUnavailableError(f"the product reports version {actual!r} but db_expected_product_version is {self.expected!r}",
                                              hint="Direct database queries are tied to a reviewed product version: update ATLASSIAN_DB_EXPECTED_PRODUCT_VERSION after reviewing the schema, or use REST.")
            cat = catalog_for(actual)
            if cat is None:
                raise BackendUnavailableError(f"no curated queries exist for product version {actual!r}", hint="Use REST; the catalog covers the versions reviewed in catalog.py.")
            self._catalog, self.verified_version = cat, actual
        return self._catalog

    async def run(self, name: str, **params: Any) -> list[dict[str, Any]]:
        q = (await self.catalog()).get(name)
        if q is None:
            raise InvalidRequestError(f"{name!r} is not a curated query", hint="Direct DB access has no free-form SQL. Available: " + ", ".join(sorted(self._catalog or {})))
        unknown = set(params) - set(q.params)
        if unknown:
            raise InvalidRequestError(f"query {name!r} has no parameter(s) {', '.join(sorted(unknown))}", hint="Parameters: " + (", ".join(q.params) or "none"))
        return await self.executor.fetch(q.sql, {p: params.get(p) for p in q.params})


class DbIssueSearcher:
    """``JiraIssueSearcher`` for counts only: ``count_issues`` accepts ``project = KEY [AND status = "Name"]``; search is not a database job."""

    def __init__(self, gate: DbGate) -> None:
        self.gate = gate

    async def search_issues(self, jql: str, *, fields: Sequence[str] | None = None, limit: int = 50, cursor: str | None = None, reconcile_issue_ids: Sequence[int] = ()) -> Page[Issue]:
        raise UnsupportedOperationError("the database backend does not return issues", operation="jira.issue.search", hint="It only counts issues; use REST or MCP to read them.")

    async def count_issues(self, jql: str) -> int:
        m = _PROJECT.match(jql or "")
        if not m and (jql or "").strip():
            raise UnsupportedOperationError(f"cannot count {jql!r} from the database", operation="jira.issue.count",
                                            hint="The curated count supports `project = KEY [AND status = \"Name\"]` (or an empty JQL for everything).")
        rows = await self.gate.run("issue_count", project=m.group(1).upper() if m else None, status=(m.group(2) if m else None))
        return int(rows[0]["n"])


class DbAnalytics:
    """``AnalyticsReader`` over the curated catalog: tables are query names; ``query`` takes ``name key=value ...`` and nothing else."""

    def __init__(self, gate: DbGate) -> None:
        self.gate = gate

    async def list_shares(self) -> list[str]:
        return ["jira-dc-database"]

    async def list_tables(self, share: str | None = None) -> list[str]:
        return sorted(await self.gate.catalog())

    async def load_table(self, table: str, *, columns: Sequence[str] | None = None, limit: int | None = None, as_: Literal["records", "pandas", "arrow"] = "records") -> Any:
        if as_ != "records":
            raise UnsupportedOperationError("the database backend returns records only", operation="analytics.table.load", hint="Use as_='records'.")
        if table not in await self.gate.catalog():
            raise NotFoundError(f"{table!r} is not a curated query", hint="Call list_tables() for the names.")
        rows = await self.gate.run(table, **{p: None for p in (await self.gate.catalog())[table].params})
        rows = [{c: r[c] for c in columns} for r in rows] if columns else rows
        return rows[:limit] if limit else rows

    async def query(self, sql: str, *, tables: Sequence[str]) -> list[dict[str, Any]]:
        try:
            parts = shlex.split(sql)
        except ValueError as exc:
            raise InvalidRequestError("could not parse the query name", hint="Pass `name key=value ...`.") from exc
        if not parts or not re.fullmatch(r"\w+", parts[0]) or re.match(r"(?i)^(select|with|insert|update|delete|drop|alter|create)$", parts[0]):
            raise PolicyDeniedError("free-form SQL is not allowed through the database backend", operation="analytics.table.query",
                                    hint="Name a curated query (list_tables()) and pass parameters as key=value; the catalog is the only SQL that ever runs.")
        params = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
        return await self.gate.run(parts[0], **params)


class DatabaseBackend:
    kind: ClassVar[BackendKind] = BackendKind.DATABASE
    deployments: ClassVar[frozenset[Deployment]] = frozenset({Deployment.DATA_CENTER})

    def __init__(self, executor: DbExecutor, *, expected_version: str, version_probe: Callable[[], Awaitable[str]]) -> None:
        self.executor = executor
        self.gate_ = DbGate(executor, expected_version, version_probe)
        self.searcher, self.analytics = DbIssueSearcher(self.gate_), DbAnalytics(self.gate_)
        self._parts: tuple[object, ...] = (self.searcher, self.analytics)

    def implementation_for(self, protocol: type) -> object | None:
        return next((p for p in self._parts if isinstance(p, protocol)), None)

    def supported_operations(self) -> frozenset[str]:
        return OPS

    def gate(self, policy: AccessPolicy, op: Operation) -> str | None:
        if not policy.allow_direct_db:
            return f"direct database access is not enabled; {op.id} was not routed to the DATABASE backend (set allow_direct_db=True to opt in)"
        return None

    @property
    def warnings(self) -> tuple[str, ...]:
        return ("direct database access is opt-in and discouraged: read-only curated queries, tied to a reviewed product version",
                f"verified product version: {self.gate_.verified_version or 'not yet verified'}")

    async def health(self) -> BackendHealth:
        try:
            await self.gate_.catalog()
        except Exception as exc:                                       # health must never raise
            return BackendHealth("unavailable", f"{type(exc).__name__}: {exc}")
        return BackendHealth("ready", f"product version {self.gate_.verified_version} matches; {len(await self.gate_.catalog())} curated read-only queries")

    async def aclose(self) -> None:
        await self.executor.aclose()


class DatabaseBackendFactory:
    kind: ClassVar[BackendKind] = BackendKind.DATABASE
    deployments: ClassVar[frozenset[Deployment]] = frozenset({Deployment.DATA_CENTER})
    extra: ClassVar[str | None] = "db"

    def __init__(self, executor_factory: Callable[[AtlassianConfig], DbExecutor] | None = None, version_probe_factory: Callable[[AtlassianConfig, CoreServices], Callable[[], Awaitable[str]]] | None = None,
                 *, has_sqlalchemy: Callable[[], bool] | None = None) -> None:
        self._executor_factory, self._probe_factory, self._has = executor_factory, version_probe_factory, has_sqlalchemy

    def _sa_ok(self) -> bool:
        if self._has is not None:
            return self._has()
        import importlib.util

        return importlib.util.find_spec("sqlalchemy") is not None

    def availability(self, config: AtlassianConfig) -> Availability:
        if config.deployment is not Deployment.DATA_CENTER:
            return Availability(False, "direct database access exists for Data Center only; refused on Cloud")
        if not config.allow_direct_db:
            return Availability(False, "direct database access is opt-in: set ATLASSIAN_ALLOW_DIRECT_DB=true (discouraged; REST is the supported path)")
        if not config.db_expected_product_version:
            return Availability(False, "ATLASSIAN_DB_EXPECTED_PRODUCT_VERSION is not set: queries are tied to a reviewed product version that must match serverInfo")
        if config.db_url is None:
            return Availability(False, "ATLASSIAN_DB_URL is not set (use a read-only database account)")
        if self._executor_factory is None and not self._sa_ok():
            return Availability(False, "pip install 'atlian-assiant[db]' and the driver for your database")
        return Availability(True)

    def create(self, config: AtlassianConfig, core: CoreServices) -> DatabaseBackend:
        avail = self.availability(config)
        if not avail.ok:
            raise BackendUnavailableError(f"database unavailable: {avail.reason}", hint=avail.reason)
        assert config.db_expected_product_version and config.db_url
        executor = self._executor_factory(config) if self._executor_factory else SqlAlchemyExecutor(config.db_url.get_secret_value())
        probe = self._probe_factory(config, core) if self._probe_factory else _rest_dc_version_probe(config, core)
        return DatabaseBackend(executor, expected_version=config.db_expected_product_version, version_probe=probe)


def _rest_dc_version_probe(config: AtlassianConfig, core: CoreServices) -> Callable[[], Awaitable[str]]:
    async def probe() -> str:
        from ..rest_dc import RestDcBackendFactory

        backend = RestDcBackendFactory().create(config, core)
        info = await backend.jira.server_info()
        return str(info.version)
    return probe


__all__ = ["OPS", "DatabaseBackend", "DatabaseBackendFactory", "DbAnalytics", "DbGate", "DbIssueSearcher"]
