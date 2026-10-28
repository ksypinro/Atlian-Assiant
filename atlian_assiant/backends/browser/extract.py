"""Selector-driven extraction from a rendered page (design 10.8). Selectors live in the versioned ``selectors.yaml``, each with fallbacks.

Page spec keys: ``ready`` (container selector), ``rows`` (list pages), ``fields`` (dotted paths with ``:int|:float|:bool``, ``[]`` for lists, ``?`` for optional;
a mapping entry declares nested rows) and ``selectors`` (per-field overrides, primary first). Default selectors for field ``p`` of page ``P`` are
``[data-testid='P.p']`` then ``[data-field='p']``. A required field that no selector finds raises ``SelectorDriftError``: the UI changed, so update the file.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import Any

from ...core.errors import BackendUnavailableError, SelectorDriftError
from .dom import Node, parse

_FIELD = re.compile(r"^(?P<path>[\w.]+)(?P<list>\[\])?(?::(?P<type>int|float|bool|str|html))?(?P<opt>\?)?$")


@dataclass(frozen=True)
class FieldSpec:
    path: str
    is_list: bool = False
    type: str = "str"
    optional: bool = False
    rows: tuple[str, ...] = ()                       # nested rows (list of dicts)
    fields: tuple[FieldSpec, ...] = ()


def parse_field(entry: Any) -> FieldSpec:
    if isinstance(entry, Mapping):
        (name, body), = entry.items()
        base = parse_field(name)
        rows = body["rows"]
        return FieldSpec(base.path, True, "dict", base.optional, tuple([rows] if isinstance(rows, str) else rows), tuple(parse_field(f) for f in body["fields"]))
    m = _FIELD.match(str(entry))
    if not m:
        raise ValueError(f"bad field spec {entry!r}")
    return FieldSpec(m["path"], bool(m["list"]), m["type"] or "str", bool(m["opt"]))


@dataclass(frozen=True)
class PageSpec:
    name: str
    ready: tuple[str, ...] = ()
    rows: tuple[str, ...] = ()
    fields: tuple[FieldSpec, ...] = ()
    selectors: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def candidates(self, path: str, prefix: str | None = None) -> tuple[str, ...]:
        key = f"{prefix}.{path}" if prefix else path
        override = self.selectors.get(key)
        return override if override else (f"[data-testid='{self.name}.{key}']", f"[data-field='{key}']")


def _tuple(x: Any) -> tuple[str, ...]:
    return () if x is None else ((x,) if isinstance(x, str) else tuple(x))


@dataclass(frozen=True)
class Selectors:
    version: int
    pages: Mapping[str, PageSpec]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Selectors:
        pages = {}
        for name, body in data["pages"].items():
            rows = _tuple(body.get("rows"))
            pages[name] = PageSpec(name, _tuple(body.get("ready")), rows or (), tuple(parse_field(f) for f in body.get("fields", [])),
                                   {k: _tuple(v) for k, v in (body.get("selectors") or {}).items()})
        return cls(int(data.get("version", 1)), pages)

    @classmethod
    @cache
    def load(cls) -> Selectors:
        try:
            import yaml
        except ImportError as exc:
            raise BackendUnavailableError("the browser backend reads selectors.yaml and needs PyYAML", hint="pip install 'atlian-assiant[browser]'") from exc
        text = resources.files(__package__).joinpath("selectors.yaml").read_text(encoding="utf-8")
        return cls.from_dict(yaml.safe_load(text))

    def page(self, name: str) -> PageSpec:
        try:
            return self.pages[name]
        except KeyError:
            raise SelectorDriftError(f"selectors.yaml has no page spec {name!r}", hint="Add the page to selectors.yaml.") from None



def _coerce(raw: str, type_: str) -> Any:
    if type_ == "int":
        return int(re.sub(r"[^\d-]", "", raw))
    if type_ == "float":
        return float(raw)
    if type_ == "bool":
        return raw.strip().lower() in ("true", "yes", "1", "released", "done")
    return raw


def _first(node: Node, candidates: Sequence[str]) -> list[Node]:
    for sel in candidates:
        found = node.select(sel)
        if found:
            return found
    return []


def _set(out: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    for p in parts[:-1]:
        out = out.setdefault(p, {})
    out[parts[-1]] = value


def _fields(node: Node, spec: PageSpec, fields: Sequence[FieldSpec], prefix: str | None, where: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in fields:
        full = f"{prefix}.{f.path}" if prefix else f.path
        if f.rows:
            rows = _first(node, f.rows or spec.candidates(f.path, prefix))
            _set(out, f.path, [_fields(r, spec, f.fields, full, where) for r in rows])
            continue
        nodes = _first(node, spec.candidates(f.path, prefix))
        if not nodes:
            if f.optional or f.is_list:
                if f.is_list:
                    _set(out, f.path, [])
                continue
            raise SelectorDriftError(f"{where}: no selector found field {full!r} (tried {', '.join(spec.candidates(f.path, prefix))})",
                                     hint="The Jira/Confluence UI changed. Update selectors.yaml (bump its version) instead of guessing; the page is not trusted until then.")
        values = [n.inner_html() if f.type == "html" else _coerce(n.text(), f.type) for n in nodes]
        _set(out, f.path, values if f.is_list else values[0])
    return out


def extract_object(html: str, spec: PageSpec) -> dict[str, Any]:
    root = parse(html)
    if spec.ready and not _first(root, spec.ready):
        raise SelectorDriftError(f"{spec.name}: the page container {spec.ready[0]!r} is missing", hint="Either the page did not load for this user (no access / not found) or the UI changed: check selectors.yaml.")
    return _fields(root, spec, spec.fields, None, spec.name)


def extract_rows(html: str, spec: PageSpec) -> list[dict[str, Any]]:
    root = parse(html)
    if spec.ready and not _first(root, spec.ready):
        raise SelectorDriftError(f"{spec.name}: the list container {spec.ready[0]!r} is missing", hint="Either the page did not load for this user or the UI changed: check selectors.yaml.")
    rows = _first(root, spec.rows or (f"[data-testid='{spec.name}.row']", f"[data-row='{spec.name}']"))
    return [_fields(r, spec, spec.fields, None, spec.name) for r in rows]
