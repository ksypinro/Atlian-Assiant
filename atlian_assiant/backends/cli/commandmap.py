"""Command maps: canonical operation -> argv template for a third-party CLI (design 10.5-10.7).

A ``Command`` answers the REST-shaped request one operation emits (``method`` + ``path``). Forward (``render``) turns that request into argv
(+ optional JSON on stdin); ``parse`` is the exact inverse and lets a fake binary be generated from the same table, so the table is tested in both
directions. Everything about a CLI that cannot be verified offline lives in the per-tool map files (VER-03 / VER-04 / VER-05), nowhere else.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ...core.errors import InvalidRequestError, UnsupportedOperationError
from ...core.http import RequestSpec

Token = str | tuple[str, str]               # "literal" / "{var}" / ("--flag", "{var}") optional pair (omitted when the variable is absent)
_VAR = re.compile(r"\{([\w-]+)\}")


@dataclass(frozen=True)
class Command:
    op: str
    argv: tuple[Token, ...]
    method: str = "GET"
    path: str = ""                          # REST path template this command answers, e.g. "/issue/{key}/comment"
    wrap: str = "raw"                       # how stdout is shaped into the REST envelope (see ``shape``)
    body: bool = False                      # spec.json is sent to the CLI on stdin as JSON
    kind: str = "json"                      # json | download | upload | csv
    surface: str = "jira"                   # jira | agile | jsm | conf_v2 | conf_v1 (which REST surface the request belongs to)
    query: tuple[str, ...] = ()             # variables that are query params on non-GET requests (inverse mapping)
    jvars: tuple[tuple[str, str], ...] = ()  # variable -> dotted path in the JSON body ("fields.summary"; a trailing "[]" marks a list joined with commas)
    strict: bool = False                    # with jvars: refuse (Unsupported) when the body carries anything the flags cannot express
    requires: tuple[str, ...] = ()          # flags the installed CLI must advertise (CapabilityProbe)
    files: tuple[str, ...] = ()             # variables passed as a path to a private temp file holding the value (kept out of argv / `ps`)
    fixed: tuple[tuple[str, str], ...] = ()  # REST query params that literal flags imply (e.g. --detail full -> body-format=storage); used by the inverse

    @property
    def prefix(self) -> tuple[str, ...]:
        """Leading literal words (``jira workitem search``): what ``--help`` is run on for the capability probe."""
        out: list[str] = []
        for t in self.argv:
            if isinstance(t, tuple) or t.startswith("-") or _VAR.search(t):
                break
            out.append(t)
        return tuple(out)


def _dig(obj: Any, path: str) -> Any:
    for part in path.removesuffix("[]").split("."):
        if not isinstance(obj, Mapping) or part not in obj:
            return None
        obj = obj[part]
    return obj


def _put(obj: dict[str, Any], path: str, value: Any) -> None:
    parts = path.removesuffix("[]").split(".")
    for part in parts[:-1]:
        obj = obj.setdefault(part, {})
    obj[parts[-1]] = value.split(",") if path.endswith("[]") else value


def _leaves(obj: Any, prefix: str = "") -> list[str]:
    if isinstance(obj, Mapping):
        return [leaf for k, v in obj.items() for leaf in _leaves(v, f"{prefix}.{k}" if prefix else str(k))]
    return [prefix] if obj not in (None, [], {}) else []


def _s(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(map(str, value))
    if isinstance(value, dict):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def _path_regex(template: str) -> re.Pattern[str]:
    return re.compile("^" + _VAR.sub(lambda m: f"(?P<{m.group(1)}>[^/]+)", re.escape(template).replace(r"\{", "{").replace(r"\}", "}")) + "$")


class CommandMap:
    def __init__(self, tool: str, commands: Iterable[Command], *, envelope: str | None = None) -> None:
        self.tool = tool
        self.envelope = envelope                # key of the payload inside the CLI's JSON envelope (twg: {"apiVersion", ..., "data"})
        self.all: list[Command] = []
        self._by_op: dict[str, list[Command]] = {}
        for c in commands:
            if any(x.op == c.op and x.path == c.path for x in self.all):
                raise ValueError(f"{tool}: duplicate command for {c.op} {c.path}")
            self.all.append(c)
            self._by_op.setdefault(c.op, []).append(c)

    def __contains__(self, op: str) -> bool:
        return op in self._by_op

    @property
    def commands(self) -> dict[str, list[Command]]:
        return self._by_op

    @property
    def operations(self) -> frozenset[str]:
        return frozenset(self._by_op)

    def select(self, op: str, path: str) -> Command | None:
        """One operation can be several commands (one per REST request shape, e.g. ``/issuetype`` and ``/project/{p}``): pick the one answering ``path``."""
        for c in self._by_op.get(op, ()):
            if not c.path or _path_regex(c.path).match(path):
                return c
            if c.kind == "download" and re.match(r"^/attachments?/content/[^/]+$", path):     # the second request of a download: the bytes
                return c
        return None

    # ------------------------------------------------------------------------------------------------ forward
    def variables(self, cmd: Command, spec: RequestSpec) -> dict[str, str]:
        out: dict[str, str] = {}
        if cmd.path:
            m = _path_regex(cmd.path).match(spec.path)
            if m is None:
                raise UnsupportedOperationError(f"{cmd.op}: request {spec.path!r} does not match the mapped command path {cmd.path!r}", operation=cmd.op,
                                                hint="This request shape is not available through the CLI backend.")
            out.update(m.groupdict())
        for k, v in spec.params.items():
            if v is not None:
                out.setdefault(k, _s(v))
        if isinstance(spec.json, Mapping) and not cmd.body:
            for k, v in spec.json.items():
                if v is not None and not isinstance(v, Mapping):
                    out.setdefault(k, _s(v))
            for name, jp in cmd.jvars:
                value = _dig(spec.json, jp)
                if value is not None:
                    out.setdefault(name, _s(value))
            if cmd.strict:
                covered = {jp.removesuffix("[]") for _, jp in cmd.jvars}
                extra = [leaf for leaf in _leaves(spec.json) if leaf not in covered]
                if extra:
                    raise UnsupportedOperationError(f"{cmd.op}: the {self.tool} flags cannot express {', '.join(extra)}", operation=cmd.op,
                                                    hint="Use the REST or MCP backend for custom fields and rich bodies; the router falls back.")
        if "maxResults" in out:
            out.setdefault("limit", out["maxResults"])
        if "startAt" in out:
            out.setdefault("start", out["startAt"])
        return out

    def render(self, cmd: Command, spec: RequestSpec, extra: Mapping[str, str] | None = None) -> tuple[list[str], str | None]:
        vars_ = {**self.variables(cmd, spec), **(extra or {})}
        argv: list[str] = []
        for tok in cmd.argv:
            if isinstance(tok, tuple):
                flag, template = tok
                names = _VAR.findall(template)
                if all(n in vars_ for n in names):
                    argv += [flag, _VAR.sub(lambda m: vars_[m.group(1)], template)]
                continue
            missing = [n for n in _VAR.findall(tok) if n not in vars_]
            if missing:
                raise InvalidRequestError(f"{cmd.op}: missing {', '.join(missing)} for the {self.tool} command", operation=cmd.op,
                                          hint=f"Pass {missing[0]} for this operation.")
            argv.append(_VAR.sub(lambda m: vars_[m.group(1)], tok))
        stdin = json.dumps(spec.json, separators=(",", ":")) if cmd.body and spec.json is not None else None
        return argv, stdin

    # ------------------------------------------------------------------------------------------------ inverse (fake binaries, round-trip tests)
    def parse(self, argv: Sequence[str], stdin: str | None = None) -> tuple[Command, RequestSpec, dict[str, str]] | None:
        for cmd in self.all:
            vars_ = _match(cmd.argv, list(argv))
            if vars_ is not None:
                return cmd, self._spec(cmd, dict(vars_), stdin), vars_
        return None

    @staticmethod
    def _spec(cmd: Command, vars_: dict[str, str], stdin: str | None) -> RequestSpec:
        from ...core.urls import ApiSurface

        surface = {"jira": ApiSurface.JIRA_PLATFORM, "agile": ApiSurface.JIRA_AGILE, "jsm": ApiSurface.JSM, "conf_v2": ApiSurface.CONFLUENCE_V2,
                   "conf_v1": ApiSurface.CONFLUENCE_V1}[cmd.surface]
        names = set(_VAR.findall(cmd.path))
        path = _VAR.sub(lambda m: vars_[m.group(1)], cmd.path)
        rest = {**dict(cmd.fixed), **{k: v for k, v in vars_.items() if k not in names and k not in ("limit", "start")}}
        body = json.loads(stdin) if stdin else None
        if cmd.jvars:
            body = body or {}
            for name, jp in cmd.jvars:
                if name in rest:
                    _put(body, jp, rest.pop(name))
        if cmd.method == "GET" or cmd.query:
            return RequestSpec(cmd.method, surface, path, rest, json=body, operation=cmd.op)
        if body is None and rest:
            body = rest
        return RequestSpec(cmd.method, surface, path, {}, json=body, operation=cmd.op)


def _match(template: Sequence[Token], argv: list[str]) -> dict[str, str] | None:
    vars_: dict[str, str] = {}
    i = 0

    def bind(tok: str, value: str) -> bool:
        m = _VAR.fullmatch(tok)
        if m:
            vars_[m.group(1)] = value
            return True
        return tok == value

    for tok in template:
        if isinstance(tok, tuple):
            flag, template_value = tok
            if i < len(argv) and argv[i] == flag and i + 1 < len(argv):
                if not bind(template_value, argv[i + 1]):
                    return None
                i += 2
            continue
        if i >= len(argv) or not bind(tok, argv[i]):
            return None
        i += 1
    return vars_ if i == len(argv) else None


# ---------------------------------------------------------------------------------------------------- stdout -> REST envelope
def shape(wrap: str, data: Any, spec: RequestSpec) -> Any:
    """CLIs print the whole result; the shared pagination strategies expect a page envelope. ``wrap`` names the envelope."""
    if wrap == "raw":
        return data
    if wrap == "v2content":                                # MCP/twg v2 content: body {format, value}, version + author under metadata
        if not isinstance(data, dict):
            return data
        meta = data.get("metadata") or {}
        body = data.get("body") or {}
        rest = {k: v for k, v in data.items() if k not in ("body", "metadata")}
        rest.update(version=data.get("version") or meta.get("version") or {}, authorId=data.get("authorId") or meta.get("authorId"))
        if isinstance(body, dict) and "format" in body:
            rest["body"] = {"storage": {"value": body.get("value") or ""}} if body["format"] in ("html", "storage") else {}
        return rest
    if wrap == "twgcontext":                               # twg `context get`: related objects under one of several keys
        body = data if isinstance(data, dict) else {"related": data}
        items: list[Any] = next((body[k] for k in ("related", "items", "nodes", "context", "results") if isinstance(body.get(k), list)), [])
        limit = int((spec.json or {}).get("maxItems") or 50) if isinstance(spec.json, Mapping) else 50
        return {"related": [{"ari": o.get("ari") or o.get("id"), "type": o.get("type") or o.get("entityType"), "title": o.get("title") or o.get("name"),
                             "url": o.get("url")} for o in items if isinstance(o, dict)][:limit], "relationships": list(body.get("relationships") or body.get("edges") or [])}
    if wrap == "twgsearch":                                # twg `rovo search`
        body = data if isinstance(data, dict) else {"results": data}
        items = next((body[k] for k in ("results", "items", "hits") if isinstance(body.get(k), list)), [])
        return {"results": [{"title": h.get("title") or h.get("name") or "", "url": h.get("url"), "product": h.get("product") or h.get("app"),
                             "snippet": h.get("snippet") or h.get("excerpt") or h.get("description"), "score": h.get("score")}
                            for h in items if isinstance(h, dict)]}
    kind, _, key = wrap.partition(":")
    items = data if isinstance(data, list) else _items(data, key)
    p = spec.params
    body = spec.json if isinstance(spec.json, Mapping) else {}
    if kind == "fields":                                   # issue envelope around a bare list: {"fields": {key: [...]}}
        return {"fields": {key: items}}
    if kind == "obj":                                      # object envelope around a bare list: {key: [...]}
        return {key: items}
    if kind == "offset":                                   # Jira classic + Agile: startAt / maxResults / total
        start, limit = int(p.get("startAt", 0)), int(p.get("maxResults", 50))
        return {key: items[start:start + limit], "startAt": start, "maxResults": limit, "total": len(items), "isLast": start + limit >= len(items)}
    if kind == "token":                                    # enhanced JQL search: nextPageToken
        start, limit = int(body.get("nextPageToken") or 0), int(body.get("maxResults") or 50)
        more = start + limit < len(items)
        return {key: items[start:start + limit], "nextPageToken": str(start + limit) if more else None, "isLast": not more}
    if kind == "jsm":                                      # servicedeskapi: start / limit / isLastPage
        start, limit = int(p.get("start", 0)), int(p.get("limit", 50))
        chunk = items[start:start + limit]
        return {"values": chunk, "start": start, "size": len(chunk), "limit": limit, "isLastPage": start + limit >= len(items)}
    if kind == "cql":                                      # Confluence v1 search: start / limit / _links.next
        start, limit = int(p.get("start", 0)), int(p.get("limit", 25))
        chunk = items[start:start + limit]
        more = start + limit < len(items)
        return {"results": chunk, "start": start, "limit": limit, "size": len(chunk), "totalSize": len(items), "_links": {"next": "more"} if more else {}}
    if kind == "link":                                     # Confluence v2: follow _links.next verbatim
        start, limit = int(p.get("cursor", 0) or 0), int(p.get("limit", 25))
        chunk = items[start:start + limit]
        more = start + limit < len(items)
        return {"results": chunk, "_links": {"next": f"/wiki/api/v2{spec.path}?cursor={start + limit}&limit={limit}"} if more else {}}
    raise ValueError(f"unknown wrap {wrap!r}")


def _items(data: Any, key: str) -> list[Any]:
    """The list inside a CLI's object output: ``key`` itself, its plural or another casing (acli prints ``attachments`` / ``issueLinks``), else ``values``."""
    if not isinstance(data, dict):
        return []
    wanted = {key.lower(), key.lower() + "s"}
    for k, v in data.items():
        if k.lower() in wanted and isinstance(v, list):
            return v
    return list(data.get("values") or [])


def unshape(wrap: str, data: Any) -> Any:
    """Inverse used by fake binaries: print the bare list, as a CLI would."""
    if wrap == "raw" or not isinstance(data, dict):
        return data
    if wrap == "v2content":
        storage = ((data.get("body") or {}).get("storage") or {}).get("value")
        out = {k: v for k, v in data.items() if k not in ("body", "version", "authorId")}
        out["metadata"] = {"version": data.get("version"), "authorId": data.get("authorId")}
        if storage is not None:
            out["body"] = {"format": "html", "value": storage}
        return out
    if wrap in ("twgcontext", "twgsearch"):
        return data
    kind, _, key = wrap.partition(":")
    if kind == "fields":
        return (data.get("fields") or {}).get(key, [])
    if kind == "obj":
        return data.get(key, [])
    for k in (key, "values", "results", "issues"):
        if k in data and isinstance(data[k], list):
            return data[k]
    return data


@dataclass
class ProbeReport:
    version: str | None = None
    available: frozenset[str] = frozenset()
    dropped: dict[str, str] = field(default_factory=dict)
