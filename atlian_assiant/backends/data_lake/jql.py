"""A deliberately small JQL / CQL subset translated to SQL (design 11: Data Lake search is ``◐ SQL``). Anything outside the subset is refused so the router falls
back to REST instead of returning a wrong answer."""
from __future__ import annotations

import re
from typing import Any

from ...core.errors import UnsupportedOperationError

_ORDER = re.compile(r"\border\s+by\b", re.I)
_AND = re.compile(r"\s+and\s+", re.I)
_CLAUSE = re.compile(r"""^\s*(?P<field>[\w.]+)\s*(?P<op>!=|>=|<=|=|>|<|~|\bnot\s+in\b|\bin\b|\bis\s+not\b|\bis\b)\s*(?P<value>.+?)\s*$""", re.I | re.S)
_UNSUPPORTED_HINT = ("Data Lake search supports a JQL subset: `field = value` clauses joined by AND (project, status, assignee, reporter, issuetype, priority, key, labels, summary ~, "
                     "created/updated comparisons) and ORDER BY. Use REST or MCP for the full language.")

JIRA_FIELDS = {"project": "project_key", "status": "status", "issuetype": "issue_type", "type": "issue_type", "priority": "priority", "key": "issue_key", "issuekey": "issue_key",
               "reporter": "reporter_name", "created": "created", "updated": "updated", "summary": "summary", "description": "description"}
CQL_FIELDS = {"space": "space_key", "title": "title", "id": "page_id", "type": "'page'", "text": "body_storage"}


def _values(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1]
    return [v.strip().strip('"\'') for v in re.findall(r"""\"[^\"]*\"|'[^']*'|[^,]+""", raw) if v.strip()]


def _split(query: str) -> tuple[str, str]:
    m = _ORDER.search(query)
    return (query[: m.start()], query[m.end():]) if m else (query, "")


def _clauses(where: str, op_hint: str) -> list[re.Match[str]]:
    if re.search(r"\bor\b|\bnot\b(?!\s+in)", where, re.I) and not re.search(r"""["'][^"']*\b(or|not)\b[^"']*["']""", where, re.I):
        raise UnsupportedOperationError(f"{op_hint}: OR / NOT are outside the Data Lake query subset", operation=op_hint, hint=_UNSUPPORTED_HINT)
    out = []
    for part in [p for p in _AND.split(where.strip()) if p.strip()]:
        m = _CLAUSE.match(part)
        if not m:
            raise UnsupportedOperationError(f"{op_hint}: cannot translate {part.strip()!r}", operation=op_hint, hint=_UNSUPPORTED_HINT)
        out.append(m)
    return out


def jql_to_sql(jql: str) -> tuple[str, list[Any], str]:
    """-> (WHERE fragment with ``?`` placeholders, parameters, ORDER BY fragment)."""
    where, order = _split(jql or "")
    sql: list[str] = []
    params: list[Any] = []
    for m in _clauses(where, "jira.issue.search"):
        field, op, raw = m["field"].lower(), re.sub(r"\s+", " ", m["op"].lower()), m["value"]
        is_function = "(" in raw and op in ("=", "!=", "~", ">", "<", ">=", "<=")
        is_relative_date = bool(re.match(r"^\s*-\d", raw))
        if is_function or is_relative_date:
            raise UnsupportedOperationError(f"jira.issue.search: functions and relative dates ({raw}) are outside the Data Lake subset", operation="jira.issue.search", hint=_UNSUPPORTED_HINT)
        if field == "assignee":
            vals = _values(raw)
            if op in ("is", "is not"):
                sql.append("assignee_id IS NULL" if op == "is" else "assignee_id IS NOT NULL")
                continue
            if op not in ("=", "!=", "in", "not in"):
                raise UnsupportedOperationError("jira.issue.search: assignee supports =, !=, in, is empty", operation="jira.issue.search", hint=_UNSUPPORTED_HINT)
            marks = ", ".join("?" for _ in vals)
            clause = f"(assignee_id IN ({marks}) OR assignee_name IN ({marks}) OR assignee_email IN ({marks}))"
            sql.append(clause if op in ("=", "in") else f"NOT {clause}")
            params += vals * 3
            continue
        if field == "labels":
            if op not in ("=", "in"):
                raise UnsupportedOperationError("jira.issue.search: labels supports = and in", operation="jira.issue.search", hint=_UNSUPPORTED_HINT)
            vals = _values(raw)
            sql.append("(" + " OR ".join("(',' || labels || ',') LIKE ?" for _ in vals) + ")")
            params += [f"%,{v},%" for v in vals]
            continue
        col = JIRA_FIELDS.get(field)
        if col is None:
            raise UnsupportedOperationError(f"jira.issue.search: field {field!r} is outside the Data Lake subset", operation="jira.issue.search", hint=_UNSUPPORTED_HINT)
        if op in ("is", "is not"):
            sql.append(f"{col} IS NULL" if op == "is" else f"{col} IS NOT NULL")
        elif op in ("in", "not in"):
            vals = _values(raw)
            sql.append(f"{col} {'NOT ' if op == 'not in' else ''}IN ({', '.join('?' for _ in vals)})")
            params += vals
        elif op == "~":
            sql.append(f"{col} LIKE ?")
            params.append(f"%{_values(raw)[0]}%")
        else:
            sql.append(f"{col} {op} ?")
            params.append(_values(raw)[0])
    order_sql = ""
    if order.strip():
        cols = []
        for part in order.split(","):
            om = re.match(r"^\s*(\w+)(?:\s+(asc|desc))?\s*$", part, re.I)
            c = JIRA_FIELDS.get(om.group(1).lower()) if om else None
            if om is None or c is None:
                raise UnsupportedOperationError(f"jira.issue.search: cannot order by {part.strip()!r}", operation="jira.issue.search", hint=_UNSUPPORTED_HINT)
            cols.append(f"{c} {(om.group(2) or 'asc').upper()}")
        order_sql = " ORDER BY " + ", ".join(cols)
    return (" AND ".join(sql) or "1=1"), params, order_sql


def cql_to_sql(cql: str) -> tuple[str, list[Any]]:
    where, order = _split(cql or "")
    if order.strip():
        raise UnsupportedOperationError("confluence.search.cql: ORDER BY is outside the Data Lake subset", operation="confluence.search.cql", hint=_UNSUPPORTED_HINT)
    sql: list[str] = []
    params: list[Any] = []
    for m in _clauses(where, "confluence.search.cql"):
        field, op, raw = m["field"].lower(), m["op"].lower(), m["value"]
        if field == "type":
            if _values(raw) != ["page"] or op != "=":
                raise UnsupportedOperationError("confluence.search.cql: only type = page is available in the lake", operation="confluence.search.cql", hint=_UNSUPPORTED_HINT)
            continue
        col = CQL_FIELDS.get(field)
        if col is None or op not in ("=", "~"):
            raise UnsupportedOperationError(f"confluence.search.cql: cannot translate {m.group(0).strip()!r}", operation="confluence.search.cql", hint=_UNSUPPORTED_HINT)
        sql.append(f"{col} {'LIKE' if op == '~' else '='} ?")
        params.append(f"%{_values(raw)[0]}%" if op == "~" else _values(raw)[0])
    return (" AND ".join(sql) or "1=1"), params
