"""SQL mirror stores (design 15): ONE implementation over a tiny async executor, used for SQLite (stdlib) and Postgres (JSONB, via SQLAlchemy).

Schema: ``mirror_documents(kind, id, version, scope, parent, deleted, document, updated_at)``. A tombstone keeps the row (``deleted`` = true) so the version gate can
tell "deleted" from "never seen", and ``descendants`` walks ``parent`` links with a recursive CTE.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

import anyio.to_thread

from ...core.errors import BackendUnavailableError


class SqlExecutor(Protocol):
    """Named parameters (``:name``). ``execute`` commits."""

    async def execute(self, sql: str, params: Mapping[str, Any]) -> None: ...
    async def fetch(self, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]: ...
    async def aclose(self) -> None: ...


class SqliteExecutor:
    def __init__(self, path: str = ":memory:") -> None:
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = anyio.Lock()

    async def execute(self, sql: str, params: Mapping[str, Any]) -> None:
        async with self._lock:
            await anyio.to_thread.run_sync(self._run, sql, dict(params), True)

    async def fetch(self, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        async with self._lock:
            return await anyio.to_thread.run_sync(self._run, sql, dict(params), False)

    def _run(self, sql: str, params: dict[str, Any], commit: bool) -> list[dict[str, Any]]:
        cur = self._db.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()] if not commit else []
        if commit:
            self._db.commit()
        return rows

    async def aclose(self) -> None:
        self._db.close()


class SqlAlchemyExecutor:
    """Writable SQLAlchemy executor (the ``db`` extra) for Postgres. The mirror owns its table; use a dedicated schema / account."""

    def __init__(self, url: str) -> None:
        try:
            import sqlalchemy
        except ImportError as exc:
            raise BackendUnavailableError("SQLAlchemy is not installed", hint="pip install 'atlian-assiant[db]' and the driver for your database (e.g. psycopg)") from exc
        self._sa, self._engine = sqlalchemy, sqlalchemy.create_engine(url)

    def _run(self, sql: str, params: Mapping[str, Any], fetch: bool) -> list[dict[str, Any]]:
        with self._engine.begin() as conn:
            result = conn.execute(self._sa.text(sql), dict(params))
            return [dict(r) for r in result.mappings().all()] if fetch else []

    async def execute(self, sql: str, params: Mapping[str, Any]) -> None:
        await anyio.to_thread.run_sync(self._run, sql, params, False)

    async def fetch(self, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        return await anyio.to_thread.run_sync(self._run, sql, params, True)

    async def aclose(self) -> None:
        self._engine.dispose()


class SqlMirrorStore:
    """``MirrorStore`` over any ``SqlExecutor``. ``jsonb`` selects Postgres' JSONB column (the document is bound as text and cast)."""

    def __init__(self, executor: SqlExecutor, *, table: str = "mirror_documents", jsonb: bool = False) -> None:
        if not table.replace("_", "").isalnum():
            raise ValueError(f"unsafe table name {table!r}")
        self.x, self.t, self.jsonb, self._ready = executor, table, jsonb, False

    async def _init(self) -> None:
        if self._ready:
            return
        doc = "JSONB" if self.jsonb else "TEXT"
        await self.x.execute(f"CREATE TABLE IF NOT EXISTS {self.t} (kind TEXT NOT NULL, id TEXT NOT NULL, version TEXT, scope TEXT, parent TEXT, deleted BOOLEAN NOT NULL DEFAULT FALSE, "
                             f"document {doc}, updated_at TEXT, PRIMARY KEY (kind, id))", {})
        await self.x.execute(f"CREATE INDEX IF NOT EXISTS {self.t}_scope ON {self.t} (kind, scope)", {})
        await self.x.execute(f"CREATE INDEX IF NOT EXISTS {self.t}_parent ON {self.t} (parent)", {})
        self._ready = True

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    async def get_version(self, kind: str, entity_id: str) -> str | None:
        await self._init()
        rows = await self.x.fetch(f"SELECT version, deleted FROM {self.t} WHERE kind = :kind AND id = :id", {"kind": kind, "id": entity_id})
        return None if not rows or rows[0]["deleted"] in (True, 1) else str(rows[0]["version"])

    async def upsert(self, kind: str, entity_id: str, version: str, document: dict[str, Any]) -> None:
        await self._init()
        doc = "CAST(:doc AS JSONB)" if self.jsonb else ":doc"
        await self.x.execute(f"INSERT INTO {self.t} (kind, id, version, scope, parent, deleted, document, updated_at) VALUES (:kind, :id, :version, :scope, :parent, FALSE, {doc}, :now) "
                             f"ON CONFLICT (kind, id) DO UPDATE SET version = :version, scope = :scope, parent = :parent, deleted = FALSE, document = {doc}, updated_at = :now",
                             {"kind": kind, "id": entity_id, "version": version, "scope": document.get("_scope"), "parent": document.get("_parent"),
                              "doc": json.dumps(document, default=str), "now": self._now()})

    async def tombstone(self, kind: str, entity_id: str) -> None:
        await self._init()
        await self.x.execute(f"UPDATE {self.t} SET deleted = TRUE, updated_at = :now WHERE kind = :kind AND id = :id", {"kind": kind, "id": entity_id, "now": self._now()})

    async def known_ids(self, kind: str, scope: str) -> AsyncIterator[str]:
        await self._init()
        for r in await self.x.fetch(f"SELECT id FROM {self.t} WHERE kind = :kind AND scope = :scope AND deleted = FALSE ORDER BY id", {"kind": kind, "scope": scope}):
            yield str(r["id"])

    async def descendants(self, kind: str, entity_id: str) -> list[str]:
        await self._init()
        rows = await self.x.fetch(f"WITH RECURSIVE tree(kind, id) AS (SELECT kind, id FROM {self.t} WHERE parent = :root AND deleted = FALSE UNION "
                                  f"SELECT d.kind, d.id FROM {self.t} d JOIN tree t ON d.parent = t.kind || ':' || t.id WHERE d.deleted = FALSE) SELECT id FROM tree ORDER BY id",
                                  {"root": f"{kind}:{entity_id}"})
        return [str(r["id"]) for r in rows]

    async def document(self, kind: str, entity_id: str) -> dict[str, Any] | None:
        await self._init()
        rows = await self.x.fetch(f"SELECT document, deleted FROM {self.t} WHERE kind = :kind AND id = :id", {"kind": kind, "id": entity_id})
        if not rows or rows[0]["deleted"] in (True, 1):
            return None
        raw = rows[0]["document"]
        return dict(raw) if isinstance(raw, dict) else dict(json.loads(raw))

    async def aclose(self) -> None:
        await self.x.aclose()


def sqlite_store(path: str = ":memory:") -> SqlMirrorStore:
    return SqlMirrorStore(SqliteExecutor(path))


def postgres_store(url: str, *, table: str = "mirror_documents") -> SqlMirrorStore:
    return SqlMirrorStore(SqlAlchemyExecutor(url), table=table, jsonb=True)


