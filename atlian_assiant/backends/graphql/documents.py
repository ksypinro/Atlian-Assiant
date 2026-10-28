"""Stored, versioned GraphQL documents (design 10.4): ``queries/*.graphql``, each with ``# op:``, ``# requires:`` and optional ``# experimental:`` header lines.

``requires`` lists ``Type.field`` pairs the live schema must expose; schema validation (``GraphQLBackend.validate_schema``) drops an operation whose pairs are
missing or deprecated instead of failing at call time. ``experimental`` names the ``X-ExperimentalApi`` opt-ins the gateway demands for the document.
Every document is validated against the recorded live schema subset in ``schema_subset.graphql`` by the test suite (DCR-007).
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import resources


@dataclass(frozen=True)
class Document:
    name: str
    op: str
    requires: tuple[str, ...]
    text: str
    experimental: tuple[str, ...] = ()


def _parse(name: str, raw: str) -> Document:
    op, body = "", []
    requires: tuple[str, ...] = ()
    experimental: tuple[str, ...] = ()
    for line in raw.splitlines():
        if line.startswith("# op:"):
            op = line.removeprefix("# op:").strip()
        elif line.startswith("# requires:"):
            requires = tuple(x.strip() for x in line.removeprefix("# requires:").split(",") if x.strip())
        elif line.startswith("# experimental:"):
            experimental = tuple(x.strip() for x in line.removeprefix("# experimental:").split(",") if x.strip())
        else:
            body.append(line)
    if not op:
        raise ValueError(f"{name}.graphql has no '# op:' header")
    return Document(name, op, requires, "\n".join(body).strip() + "\n", experimental)


@cache
def load_documents() -> dict[str, Document]:
    out: dict[str, Document] = {}
    for entry in sorted(resources.files(__package__).joinpath("queries").iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".graphql"):
            name = entry.name.removesuffix(".graphql")
            out[name] = _parse(name, entry.read_text(encoding="utf-8"))
    return out
