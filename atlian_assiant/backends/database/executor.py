"""Read-only database executors for the DATABASE backend. ``SqlAlchemyExecutor`` (optional ``db`` extra) opens every statement in a READ ONLY transaction."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

import anyio.to_thread

from ...core.errors import BackendUnavailableError


class DbExecutor(Protocol):
    async def fetch(self, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]: ...
    async def aclose(self) -> None: ...


_READ_ONLY = {"postgresql": "SET TRANSACTION READ ONLY", "mysql": "SET SESSION TRANSACTION READ ONLY", "mariadb": "SET SESSION TRANSACTION READ ONLY",
              "oracle": "SET TRANSACTION READ ONLY", "mssql": None}


class SqlAlchemyExecutor:
    """``sqlalchemy`` is imported lazily. The read-only statement is mandatory: a dialect without one (or an unknown dialect) is refused, never run read-write."""

    def __init__(self, url: str) -> None:
        try:
            import sqlalchemy
        except ImportError as exc:
            raise BackendUnavailableError("SQLAlchemy is not installed", hint="pip install 'atlian-assiant[db]' and the driver for your database") from exc
        self._sa = sqlalchemy
        self._engine = sqlalchemy.create_engine(url)
        dialect = self._engine.dialect.name
        if dialect not in _READ_ONLY:
            raise BackendUnavailableError(f"unsupported database dialect {dialect!r}", hint="Supported: " + ", ".join(sorted(_READ_ONLY)))
        self._read_only = _READ_ONLY[dialect]

    def _run(self, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        with self._engine.connect() as conn:
            if self._read_only:
                conn.exec_driver_sql(self._read_only)
            rows = conn.execute(self._sa.text(sql), dict(params)).mappings().all()
            conn.rollback()
            return [dict(r) for r in rows]

    async def fetch(self, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        return await anyio.to_thread.run_sync(self._run, sql, params)

    async def aclose(self) -> None:
        self._engine.dispose()
