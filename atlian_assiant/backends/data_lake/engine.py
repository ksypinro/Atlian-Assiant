"""SQL engines for Data Lake tables (design 10.9): DuckDB when installed, otherwise the stdlib SQLite. Agents never get free-form SQL on the process:
statements are validated (one SELECT / WITH, no file or extension access) and run read-only over only the named tables."""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from ...core.errors import InvalidRequestError

_FORBIDDEN = re.compile(r"\b(attach|detach|pragma|copy|install|load|export|import|create|drop|alter|insert|update|delete|replace|vacuum|call|set|begin|commit|rollback|"
                        r"read_csv|read_parquet|read_json|read_text|read_blob|glob|load_extension|readfile|writefile|sniff_csv|parquet_scan|json_scan|from_json_auto)\b", re.I)
_STRING = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")
_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)


def validate_sql(sql: str) -> str:
    """One read-only statement. Strings and comments are removed before the keyword scan so literals can contain anything."""
    stripped = _COMMENT.sub(" ", _STRING.sub("''", sql)).strip().rstrip(";").strip()
    if ";" in stripped:
        raise InvalidRequestError("only one SQL statement is allowed", hint="Send a single SELECT; join tables instead of chaining statements.")
    if not re.match(r"^(select|with)\b", stripped, re.I):
        raise InvalidRequestError("only SELECT / WITH statements are allowed", hint="The Data Lake backend is read-only reporting; write a SELECT over the loaded tables.")
    hit = _FORBIDDEN.search(stripped)
    if hit:
        raise InvalidRequestError(f"the SQL keyword or function {hit.group(0)!r} is not allowed", hint="Only plain SELECT queries over the named tables run: no file, extension or DDL access.")
    return sql.strip().rstrip(";")


def _cell(v: Any) -> str | None:
    """Every lake value is held as text (like the DuckDB engine): ids compare the same whether the caller passes 42 or "42"; mappers cast back."""
    if v is None:
        return None
    return "true" if v is True else "false" if v is False else str(v)


class SqlEngine(Protocol):
    name: str

    def load(self, table: str, columns: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None: ...
    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]: ...
    def tables(self) -> list[str]: ...
    def close(self) -> None: ...


class SqliteEngine:
    name = "sqlite"

    def __init__(self) -> None:
        self._db = sqlite3.connect(":memory:", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._names: list[str] = []

    def load(self, table: str, columns: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
        if not re.fullmatch(r"\w+", table) or not all(re.fullmatch(r"\w+", c) for c in columns):
            raise InvalidRequestError(f"unsafe table or column name in {table!r}", hint="Table and column names must be plain identifiers.")
        self._db.execute(f'DROP TABLE IF EXISTS "{table}"')
        self._db.execute(f'CREATE TABLE "{table}" ({", ".join(f"{chr(34)}{c}{chr(34)}" for c in columns)})')
        self._db.executemany(f'INSERT INTO "{table}" VALUES ({", ".join("?" for _ in columns)})',
                             [tuple(_cell(r.get(c)) for c in columns) for r in rows])
        if table not in self._names:
            self._names.append(table)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        self._db.execute("PRAGMA query_only = ON")                        # reads only, whatever the SQL text says
        try:
            return [dict(r) for r in self._db.execute(sql, tuple(params)).fetchall()]
        except sqlite3.Error as exc:
            raise InvalidRequestError(f"SQL error: {exc}", hint="Check the table and column names (client.analytics.list_tables / load_table to inspect).") from exc
        finally:
            self._db.execute("PRAGMA query_only = OFF")

    def tables(self) -> list[str]:
        return list(self._names)

    def close(self) -> None:
        self._db.close()


class DuckDbEngine:
    name = "duckdb"

    def __init__(self) -> None:
        import duckdb

        self._con = duckdb.connect(":memory:", config={"enable_external_access": False})
        self._names: list[str] = []

    def load(self, table: str, columns: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
        if not re.fullmatch(r"\w+", table) or not all(re.fullmatch(r"\w+", c) for c in columns):
            raise InvalidRequestError(f"unsafe table or column name in {table!r}", hint="Table and column names must be plain identifiers.")
        self._con.execute(f'CREATE OR REPLACE TABLE "{table}" ({", ".join(f"{chr(34)}{c}{chr(34)} VARCHAR" for c in columns)})')
        if rows:
            self._con.executemany(f'INSERT INTO "{table}" VALUES ({", ".join("?" for _ in columns)})', [tuple(_cell(r.get(c)) for c in columns) for r in rows])
        if table not in self._names:
            self._names.append(table)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        try:
            cur = self._con.execute(sql, list(params))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        except Exception as exc:                                         # duckdb raises its own hierarchy
            raise InvalidRequestError(f"SQL error: {exc}", hint="Check the table and column names.") from exc

    def tables(self) -> list[str]:
        return list(self._names)

    def close(self) -> None:
        self._con.close()


def make_engine(prefer: str = "auto") -> SqlEngine:
    if prefer in ("auto", "duckdb"):
        try:
            return DuckDbEngine()
        except ImportError:
            if prefer == "duckdb":
                from ...core.errors import BackendUnavailableError

                raise BackendUnavailableError("DuckDB is not installed", hint="pip install 'atlian-assiant[duckdb]'") from None
    return SqliteEngine()
