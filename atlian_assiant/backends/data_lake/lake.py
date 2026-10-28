"""The Lake: share access + SQL engine, and the ``AnalyticsReader`` implementation (design 10.9). Every result carries ``governance_notice``."""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Literal

import anyio.to_thread

from ...core.errors import BackendUnavailableError, InvalidRequestError, NotFoundError
from .engine import SqlEngine, make_engine, validate_sql
from .schema import GOVERNANCE_NOTICE, TABLES, TableMap
from .share import ShareClient


class GovernedRecords(list[dict[str, Any]]):
    """A list of records that carries the governance notice with it."""

    governance_notice: str = GOVERNANCE_NOTICE


class GovernedNames(list[str]):
    governance_notice: str = GOVERNANCE_NOTICE


class Lake:
    def __init__(self, share: ShareClient, engine: SqlEngine | None = None, tables: TableMap | None = None, *, max_rows: int = 10_000) -> None:
        self.share, self.engine, self.map, self.max_rows = share, engine or make_engine(), tables or TableMap(), max_rows
        self._loaded: set[str] = set()

    async def ensure(self, *logical: str) -> None:
        for name in logical:
            if name in self._loaded:
                continue
            if name not in TABLES:
                raise InvalidRequestError(f"unknown lake table {name!r}", hint="Known logical tables: " + ", ".join(sorted(TABLES)))
            rows = await anyio.to_thread.run_sync(self.share.load, self.resolve(name))
            self.engine.load(name, TABLES[name], rows)
            self._loaded.add(name)

    def resolve(self, logical: str) -> str:
        """Logical name -> the share's ``share.schema.table`` (the first share table whose last segment matches)."""
        wanted = self.map.physical(logical)
        if wanted.count(".") == 2:
            return wanted
        for t in self.share.list_tables():
            if t.split(".")[-1] == wanted:
                return t
        raise NotFoundError(f"the share has no table {wanted!r}", hint="Check list_tables(); table names are tracked by VER-10 and can be remapped with TableMap.")

    async def select(self, tables: Sequence[str], sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        await self.ensure(*tables)
        return self.engine.query(sql, params)

    def refresh(self) -> None:
        self._loaded.clear()


class LakeAnalytics:
    """``AnalyticsReader``: shares, tables, ``load_table`` as records / pandas / Arrow and ``query`` (SELECT only) over named tables."""

    def __init__(self, lake: Lake) -> None:
        self.lake = lake

    async def list_shares(self) -> list[str]:
        return GovernedNames(await anyio.to_thread.run_sync(self.lake.share.list_shares))

    async def list_tables(self, share: str | None = None) -> list[str]:
        return GovernedNames(await anyio.to_thread.run_sync(self.lake.share.list_tables, share))

    async def load_table(self, table: str, *, columns: Sequence[str] | None = None, limit: int | None = None, as_: Literal["records", "pandas", "arrow"] = "records") -> Any:
        physical = self._physical(table)
        capped = min(limit, self.lake.max_rows) if limit else self.lake.max_rows
        rows = await anyio.to_thread.run_sync(self.lake.share.load, physical, list(columns) if columns else None, capped)
        if as_ == "records":
            return GovernedRecords(rows)
        try:
            import pandas
        except ImportError as exc:
            raise BackendUnavailableError("pandas is not installed", hint="pip install 'atlian-assiant[datalake]' (pandas) or use as_='records'") from exc
        frame = pandas.DataFrame(rows)
        frame.attrs["governance_notice"] = GOVERNANCE_NOTICE
        if as_ == "pandas":
            return frame
        try:
            import pyarrow
        except ImportError as exc:
            raise BackendUnavailableError("pyarrow is not installed", hint="pip install pyarrow, or use as_='records' / 'pandas'") from exc
        arrow = pyarrow.Table.from_pandas(frame)
        return arrow.replace_schema_metadata({**(arrow.schema.metadata or {}), b"governance_notice": GOVERNANCE_NOTICE.encode()})

    def _physical(self, table: str) -> str:
        if table in TABLES:
            return self.lake.resolve(table)
        if table in self.lake.share.list_tables():
            return table
        short = [t for t in self.lake.share.list_tables() if t.split(".")[-1] == table]
        if len(short) == 1:
            return short[0]
        raise NotFoundError(f"table {table!r} is not in the share", hint="Call list_tables() for the exact share.schema.table names.")

    async def query(self, sql: str, *, tables: Sequence[str]) -> list[dict[str, Any]]:
        statement = validate_sql(sql)
        if not tables:
            raise InvalidRequestError("query needs tables=[...]", hint="Name the tables the SQL reads, e.g. tables=['jira_issue'].")
        engine = make_engine("sqlite") if self.lake.engine.name == "sqlite" else make_engine()
        try:
            for t in tables:
                alias = re.sub(r"\W", "_", t.split(".")[-1])
                rows = await anyio.to_thread.run_sync(self.lake.share.load, self._physical(t), None, self.lake.max_rows)
                engine.load(alias, sorted({k for r in rows for k in r}) or ["_empty"], rows)
            return GovernedRecords(engine.query(f"SELECT * FROM ({statement}) AS q LIMIT {self.lake.max_rows}"))
        finally:
            engine.close()
