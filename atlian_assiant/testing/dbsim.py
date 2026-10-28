"""A Jira-DC-shaped SQLite database for the DATABASE backend's tests (design 10.12): the tables and columns the curated catalog reads, filled from the scenario."""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from .scenario import scenario


class SqliteExecutor:
    """Implements ``DbExecutor``. Statements run with ``PRAGMA query_only`` so a write would fail even if one slipped through, and every statement is recorded."""

    def __init__(self, raw: dict[str, Any] | None = None) -> None:
        raw = raw or scenario()
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.closed = False
        self.db.executescript("CREATE TABLE project (id INTEGER PRIMARY KEY, pkey TEXT, pname TEXT);"
                              "CREATE TABLE issuestatus (id TEXT PRIMARY KEY, pname TEXT);"
                              "CREATE TABLE jiraissue (id INTEGER PRIMARY KEY, pkey TEXT, issuenum INTEGER, PROJECT INTEGER, issuestatus TEXT, summary TEXT);")
        for p in raw["jira"]["projects"]:
            self.db.execute("INSERT INTO project VALUES (?, ?, ?)", (int(p["id"]), p["key"], p["name"]))
        for st in raw["jira"]["statuses"]:
            self.db.execute("INSERT INTO issuestatus VALUES (?, ?)", (st["id"], st["name"]))
        for i in raw["jira"]["issues"]:
            self.db.execute("INSERT INTO jiraissue VALUES (?, ?, ?, ?, ?, ?)", (int(i["id"]), i["key"], int(i["key"].split("-")[1]), int(i["fields"]["project"]["id"]),
                                                                              i["fields"]["status"]["id"], i["fields"]["summary"]))
        self.db.execute("INSERT OR IGNORE INTO issuestatus VALUES ('2', 'In Progress')")
        self.db.commit()

    async def fetch(self, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        self.statements.append((sql, dict(params)))
        self.db.execute("PRAGMA query_only = ON")
        return [dict(r) for r in self.db.execute(sql, dict(params)).fetchall()]

    async def aclose(self) -> None:
        self.closed = True
        self.db.close()
