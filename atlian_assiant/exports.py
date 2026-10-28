"""Exports synthesized from reads (design 9.6, DA 22): the ``◐`` cells of the matrix.

Works over any backend that can search issues / read Confluence content: Cloud REST, Data Center REST, MCP. Formats that need a rendering engine
(PDF, Word, space XML) are *native* server exports: they raise ``UnsupportedOperationError`` so the router falls back to the browser backend.
"""
from __future__ import annotations

import csv
import html
import io
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar, Literal

from .core.errors import InvalidRequestError, UnsupportedOperationError
from .formats.storage import markdown_to_storage
from .protocols.confluence import ConfluenceContentReader
from .protocols.jira import JiraIssueSearcher

DEFAULT_FIELDS = ("key", "summary", "status", "issue_type", "priority", "assignee", "reporter", "labels", "created", "updated")
_NATIVE = "{fmt} export needs the server's rendering engine"


def _cell(issue: Any, name: str) -> str:
    value: Any = getattr(issue, name, None)
    if value is None:
        value = (issue.fields or {}).get(name)
    if value is None:
        return ""
    if name in {"status"} or hasattr(value, "name") and not hasattr(value, "display_name"):
        return str(getattr(value, "name", value))
    if hasattr(value, "display_name"):
        return str(value.display_name or getattr(value, "username", "") or "")
    if isinstance(value, list):
        return ", ".join(map(str, value))
    return str(value)


def _csv_safe(text: str) -> str:
    """Neutralize spreadsheet formula injection: cells that start with = + - @ are prefixed with a quote."""
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


class SynthesizedJiraExporter:
    """``JiraExporter``: pages the JQL search and writes CSV (formula-injection safe)."""

    supported_operations: ClassVar[frozenset[str]] = frozenset({"export.jira.csv"})

    def __init__(self, searcher: JiraIssueSearcher, *, max_rows: int = 50_000) -> None:
        self._searcher, self._max = searcher, max_rows

    async def export_issues_csv(self, jql: str, *, fields: Sequence[str] | None = None, dest: Path) -> Path:
        cols = list(fields) if fields else list(DEFAULT_FIELDS)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(cols)
        cursor: str | None = None
        rows = 0
        while rows < self._max:
            page = await self._searcher.search_issues(jql, limit=min(100, self._max - rows), cursor=cursor)
            for issue in page.items:
                writer.writerow([_csv_safe(_cell(issue, c)) for c in cols])
                rows += 1
            cursor = page.next_cursor
            if not cursor or not page.items:
                break
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(buf.getvalue(), encoding="utf-8", newline="")
        return dest


class SynthesizedConfluenceExporter:
    """``ConfluenceExporter``: page as Markdown / HTML, space as one HTML document or a CSV page index. PDF / Word / XML are native-only."""

    supported_operations: ClassVar[frozenset[str]] = frozenset({"export.confluence.page", "export.confluence.space"})

    def __init__(self, reader: ConfluenceContentReader, *, space_pages: Any = None, max_pages: int = 500) -> None:
        self._reader, self._max, self._space_pages = reader, max_pages, space_pages

    async def export_content(self, content_id: str, *, format: Literal["pdf", "word", "html", "markdown"], dest: Path) -> Path:
        if format in ("pdf", "word"):
            raise UnsupportedOperationError(_NATIVE.format(fmt=format.upper()), operation="export.confluence.page",
                                            hint="Use the browser backend (native export) or the MCP backend; html and markdown are synthesized here.")
        page = await self._reader.get_content(content_id)
        if format == "markdown":
            text = f"# {page.title}\n\n{page.body_markdown or ''}\n"
        else:
            body = page.body_storage if page.body_storage is not None else markdown_to_storage(page.body_markdown or "")
            text = f"<!doctype html><html><head><meta charset=\"utf-8\"><title>{html.escape(page.title or '')}</title></head><body><h1>{html.escape(page.title or '')}</h1>{body}</body></html>"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        return dest

    async def export_space(self, space_key: str, *, format: Literal["pdf", "html", "xml", "csv"], dest: Path) -> Path:
        if format in ("pdf", "xml"):
            raise UnsupportedOperationError(_NATIVE.format(fmt=format.upper()), operation="export.confluence.space",
                                            hint="Use the browser backend's native space export (needs space admin).")
        if self._space_pages is None:
            raise InvalidRequestError("space export needs a way to list the pages of a space", operation="export.confluence.space")
        pages: list[Any] = []
        cursor: str | None = None
        while len(pages) < self._max:
            page = await self._space_pages(space_key, cursor)
            pages += page.items
            cursor = page.next_cursor
            if not cursor or not page.items:
                break
        dest.parent.mkdir(parents=True, exist_ok=True)
        if format == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["id", "title", "parent_id", "version", "url"])
            for p in pages:
                w.writerow([p.id, _csv_safe(p.title or ""), p.parent_id or "", p.version or "", p.url or ""])
            dest.write_text(buf.getvalue(), encoding="utf-8", newline="")
        else:
            parts = []
            for p in pages[: self._max]:
                full = await self._reader.get_content(p.id)
                body = full.body_storage if full.body_storage is not None else markdown_to_storage(full.body_markdown or "")
                parts.append(f"<section id=\"page-{html.escape(str(p.id))}\"><h1>{html.escape(full.title or '')}</h1>{body}</section>")
            dest.write_text(f"<!doctype html><html><head><meta charset=\"utf-8\"><title>{html.escape(space_key)}</title></head><body>{''.join(parts)}</body></html>", encoding="utf-8")
        return dest
