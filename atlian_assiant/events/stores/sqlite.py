"""SQLite stores (default for production single-node use): WAL mode, one file, no extra dependency."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import anyio

from ..model import ChangeEvent


def _path(url: str) -> str:
    if url == ":memory:":
        return url
    return url.removeprefix("sqlite:///")


class SqliteDb:
    """Shared connection wrapper; calls run in a worker thread so the event loop never blocks."""

    def __init__(self, url: str = "sqlite:///atlassian_events.db") -> None:
        path = _path(url)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        if path != ":memory:":
            Path(path).chmod(0o600)                  # queued payloads can contain issue data: owner-only (encryption at rest is the disk's job)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS checkpoints (stream TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS processed_events (key TEXT PRIMARY KEY, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS queue (id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'ready');
            CREATE TABLE IF NOT EXISTS dead_letters (id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL, error TEXT NOT NULL, at REAL NOT NULL);
        """)

    async def run(self, sql: str, args: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
        def _do() -> list[tuple[object, ...]]:
            return self._conn.execute(sql, args).fetchall()
        return await anyio.to_thread.run_sync(_do)

    def close(self) -> None:
        self._conn.close()


class SqliteCheckpointStore:
    def __init__(self, db: SqliteDb) -> None:
        self._db = db

    async def load(self, stream: str) -> str | None:
        rows = await self._db.run("SELECT value FROM checkpoints WHERE stream=?", (stream,))
        return str(rows[0][0]) if rows else None

    async def save(self, stream: str, value: str) -> None:
        await self._db.run("INSERT INTO checkpoints(stream,value) VALUES(?,?) ON CONFLICT(stream) DO UPDATE SET value=excluded.value", (stream, value))


class SqliteDedupStore:
    def __init__(self, db: SqliteDb) -> None:
        self._db = db

    async def seen(self, key: str) -> bool:
        rows = await self._db.run("SELECT expires FROM processed_events WHERE key=?", (key,))
        return bool(rows) and float(rows[0][0]) > time.time()  # type: ignore[arg-type]

    async def mark(self, key: str, ttl: float = 7 * 86400) -> None:
        await self._db.run("INSERT INTO processed_events(key,expires) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET expires=excluded.expires",
                           (key, time.time() + ttl))


class SqliteLocalQueue:
    """WAL-backed queue. Unacked rows are re-delivered after a restart (at-least-once)."""

    def __init__(self, db: SqliteDb) -> None:
        self._db = db

    async def put(self, event: ChangeEvent) -> str:
        await self._db.run("INSERT INTO queue(body) VALUES(?)", (event.model_dump_json(),))
        rows = await self._db.run("SELECT last_insert_rowid()")
        return str(rows[0][0])

    async def get(self) -> tuple[str, ChangeEvent] | None:
        rows = await self._db.run("SELECT id, body FROM queue WHERE state='ready' ORDER BY id LIMIT 1")
        if not rows:
            return None
        qid = rows[0][0]
        await self._db.run("UPDATE queue SET state='inflight' WHERE id=?", (qid,))
        return str(qid), ChangeEvent.model_validate_json(str(rows[0][1]))

    async def ack(self, queue_id: str) -> None:
        await self._db.run("DELETE FROM queue WHERE id=?", (int(queue_id),))

    async def requeue_inflight(self) -> int:
        rows = await self._db.run("SELECT count(*) FROM queue WHERE state='inflight'")
        await self._db.run("UPDATE queue SET state='ready' WHERE state='inflight'")
        return int(rows[0][0])  # type: ignore[call-overload, no-any-return]

    async def depth(self) -> int:
        rows = await self._db.run("SELECT count(*) FROM queue")
        return int(rows[0][0])  # type: ignore[call-overload, no-any-return]


class SqliteDeadLetterSink:
    def __init__(self, db: SqliteDb) -> None:
        self._db = db

    async def put(self, event: ChangeEvent, error: BaseException) -> None:
        await self._db.run("INSERT INTO dead_letters(body,error,at) VALUES(?,?,?)",
                           (event.model_dump_json(), f"{type(error).__name__}: {error}", time.time()))


class JsonlDeadLetterSink:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    async def put(self, event: ChangeEvent, error: BaseException) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"error": f"{type(error).__name__}: {error}", "event": json.loads(event.model_dump_json())})
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
