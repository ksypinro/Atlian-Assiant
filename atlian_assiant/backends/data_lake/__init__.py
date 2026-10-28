"""DATA_LAKE backend: Atlassian Data Lake / Delta Sharing (design 10.9). ANALYTICS access, never auto-routed for ordinary reads, results carry a governance notice.

Share access is NOT row-level and does not mirror product permissions (DA 21, 26). The lake is warehouse-style, possibly lagging data: it serves reporting questions
and a subset of reads (``◐`` in the matrix), and is refused unless the policy explicitly allows ``Access.ANALYTICS``.
"""
from __future__ import annotations

import csv
import io
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, ClassVar

from ...capabilities import Access, BackendKind, Deployment, Operation
from ...config import AtlassianConfig
from ...core.errors import BackendUnavailableError
from ...core.services import CoreServices
from ...exports import _csv_safe
from ...factory import Availability
from ...matrix import declared_operations
from ...models.common import AtlassianModel, Page
from ...policy import AccessPolicy
from ...protocols.base import BackendHealth
from ..optransport import OpBackend, OpTransport
from ..rest_cloud.agile import AgileRest
from ..rest_cloud.confluence import ConfluenceRest
from ..rest_cloud.jira import JiraRest
from ..rest_cloud.jsm import JsmRest
from .engine import SqlEngine, make_engine
from .jql import jql_to_sql
from .lake import GovernedNames, GovernedRecords, Lake, LakeAnalytics
from .ops import handlers
from .schema import GOVERNANCE_NOTICE, TABLES, TableMap
from .share import DeltaSharingClient, InMemoryShareClient, ShareClient

PARTIAL_OPS: dict[str, tuple[str, ...]] = {
    "jira-issue-get": ("jira.issue.get",),
    "jira-issue-search": ("jira.issue.search", "jira.issue.count"),
    "jira-comments-list": ("jira.issue.comments.list",),
    "jira-changelog": ("jira.issue.changelog.list",),
    "jira-worklogs": ("jira.issue.worklogs.list",),
    "jira-fields-meta": ("jira.meta.fields.list", "jira.meta.issue_types.list", "jira.meta.statuses.list", "jira.meta.priorities.list", "jira.meta.link_types.list"),
    "jira-users": ("jira.user.get", "jira.user.search", "jira.user.lookup_account_id"),
    "agile-read": ("jira.agile.board.list", "jira.agile.board.get", "jira.agile.sprint.list", "jira.agile.sprint.get", "jira.agile.sprint.issues", "jira.agile.epic.list"),
    "conf-content-get": ("confluence.content.get", "confluence.content.list", "confluence.space.get", "confluence.space.list"),
    "conf-cql-search": ("confluence.search.cql",),
}
ANALYTICS_OPS = frozenset({"analytics.share.list", "analytics.table.list", "analytics.table.load", "analytics.table.query"})
_CSV_COLUMNS = {"key": "issue_key", "summary": "summary", "status": "status", "issue_type": "issue_type", "priority": "priority", "assignee": "assignee_name",
                "reporter": "reporter_name", "labels": "labels", "created": "created", "updated": "updated", "project": "project_key"}


class GovernedPart:
    """Stamps ``governance_notice`` on every model, page and name list a part returns (SEC-07)."""

    def __init__(self, inner: object) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr
        import inspect

        if not inspect.iscoroutinefunction(attr):
            return attr

        async def call(*a: Any, **kw: Any) -> Any:
            return _govern(await attr(*a, **kw))
        return call


def _govern(result: Any) -> Any:
    if isinstance(result, (GovernedRecords, GovernedNames)):
        return result
    if isinstance(result, Page):
        result.governance_notice = GOVERNANCE_NOTICE
        for item in result.items:
            _govern(item)
    elif isinstance(result, AtlassianModel):
        result.governance_notice = GOVERNANCE_NOTICE                            # type: ignore[attr-defined]
    elif isinstance(result, list):
        if result and all(isinstance(x, str) for x in result):
            return GovernedNames(result)
        for item in result:
            _govern(item)
    return result


class LakeExporter:
    """``JiraExporter`` over the lake: a SQL query rendered as CSV (formula-injection safe). Subject to the same JQL subset as search."""

    def __init__(self, lake: Lake) -> None:
        self.lake = lake

    async def export_issues_csv(self, jql: str, *, fields: Sequence[str] | None = None, dest: Path) -> Path:
        cols = [c for c in (fields or ["key", "summary", "status", "issue_type", "priority", "assignee", "reporter", "labels", "created", "updated"])]
        unknown = [c for c in cols if c not in _CSV_COLUMNS]
        if unknown:
            from ...core.errors import InvalidRequestError

            raise InvalidRequestError(f"the lake export has no column for {', '.join(unknown)}", hint="Available: " + ", ".join(sorted(_CSV_COLUMNS)))
        where, params, order = jql_to_sql(jql)
        rows = await self.lake.select(["jira_issue"], f"SELECT * FROM jira_issue WHERE {where}{order or ' ORDER BY issue_key'}", params)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        for r in rows:
            w.writerow([_csv_safe("" if r.get(_CSV_COLUMNS[c]) is None else str(r[_CSV_COLUMNS[c]])) for c in cols])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(buf.getvalue(), encoding="utf-8", newline="")
        return dest


class DataLakeBackend(OpBackend):
    kind: ClassVar[BackendKind] = BackendKind.DATA_LAKE
    DIRECT: ClassVar[frozenset[str]] = ANALYTICS_OPS | {"export.jira.csv"}
    requires_access: ClassVar[Access] = Access.ANALYTICS

    def __init__(self, share: ShareClient, *, engine: SqlEngine | None = None, tables: TableMap | None = None, max_rows: int = 10_000) -> None:
        declared = declared_operations(self.kind, PARTIAL_OPS)
        self.lake = Lake(share, engine, tables, max_rows=max_rows)
        transport = OpTransport("data_lake", handlers(self.lake, declared), hint="The data lake serves a subset as SQL over shared tables; the router falls back to REST or MCP.")
        self.analytics, self.exports = LakeAnalytics(self.lake), LakeExporter(self.lake)
        raw: tuple[object, ...] = (JiraRest(transport), AgileRest(transport), JsmRest(transport), ConfluenceRest(transport), self.analytics, self.exports)
        super().__init__(transport, raw, declared)
        self._governed = {id(p): GovernedPart(p) for p in raw}

    def implementation_for(self, protocol: type) -> object | None:
        found = super().implementation_for(protocol)
        return None if found is None else self._governed[id(found)]

    def gate(self, policy: AccessPolicy, op: Operation) -> str | None:
        """SEC-07: the lake needs ANALYTICS access. Ordinary reads are never auto-routed here (presets) and are refused here even when forced."""
        if Access.ANALYTICS not in policy.allowed:
            return f"the Data Lake backend serves ANALYTICS access only; {op.id} was not routed to it because the policy does not allow ANALYTICS"
        return None

    @property
    def warnings(self) -> tuple[str, ...]:
        return (GOVERNANCE_NOTICE, "ANALYTICS access is opt-in and never part of a default policy; shared data may lag behind the product",
                "table and column names are unverified until VER-10 is signed (override with TableMap)")

    async def health(self) -> BackendHealth:
        try:
            shares = self.lake.share.list_shares()
        except Exception as exc:                                       # health must never raise
            return BackendHealth("unavailable", f"{type(exc).__name__}: {exc}")
        return BackendHealth("ready", f"{len(shares)} share(s); engine={self.lake.engine.name}; ANALYTICS access required")

    async def aclose(self) -> None:
        self.lake.engine.close()
        await super().aclose()


class DataLakeBackendFactory:
    kind: ClassVar[BackendKind] = BackendKind.DATA_LAKE
    deployments: ClassVar[frozenset[Deployment]] = frozenset({Deployment.CLOUD})
    extra: ClassVar[str | None] = "datalake"

    def __init__(self, share_factory: Callable[[AtlassianConfig], ShareClient] | None = None, *, has_delta_sharing: Callable[[], bool] | None = None) -> None:
        self._share_factory, self._has = share_factory, has_delta_sharing

    def _delta_ok(self) -> bool:
        if self._has is not None:
            return self._has()
        import importlib.util

        return importlib.util.find_spec("delta_sharing") is not None

    def availability(self, config: AtlassianConfig) -> Availability:
        if self._share_factory is None and not self._delta_ok():
            return Availability(False, "pip install 'atlian-assiant[datalake]'")
        if config.delta_sharing_profile is None:
            return Availability(False, "ATLASSIAN_DELTA_SHARING_PROFILE is not set: point it at the share profile file from your Atlassian data steward")
        if self._share_factory is None and not Path(str(config.delta_sharing_profile)).is_file():
            return Availability(False, f"the share profile {config.delta_sharing_profile} does not exist")
        return Availability(True)

    def create(self, config: AtlassianConfig, core: CoreServices) -> DataLakeBackend:
        avail = self.availability(config)
        if not avail.ok:
            raise BackendUnavailableError(f"data_lake unavailable: {avail.reason}", hint=avail.reason)
        share = self._share_factory(config) if self._share_factory else DeltaSharingClient(Path(str(config.delta_sharing_profile)))
        return DataLakeBackend(share, engine=make_engine())


__all__ = ["ANALYTICS_OPS", "PARTIAL_OPS", "TABLES", "DataLakeBackend", "DataLakeBackendFactory", "GovernedPart", "InMemoryShareClient", "LakeExporter", "make_engine"]
