"""Record the part of the live Atlassian GraphQL schema our stored queries use (DCR-007 evidence; the full schema is ~30 MB).

    python -m tools.gql_schema_subset --fetch      # introspect the live gateway (needs ATLASSIAN_SITE / EMAIL / API_TOKEN) and rewrite the subset
    python -m tools.gql_schema_subset --check      # exit 1 when a stored query does not validate against the recorded subset

The subset keeps every type a stored query touches, but only the fields the queries select (plus full input objects and enums for arguments), so
graphql-core can validate the documents offline. Interfaces keep the fields their implementations need. Requires the dev dependency ``graphql-core``.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUBSET = ROOT / "tests" / "backends" / "fixtures" / "agg_schema_subset.graphql"


def _documents() -> list[str]:
    sys.path.insert(0, str(ROOT))
    from atlian_assiant.backends.graphql.documents import load_documents

    load_documents.cache_clear()
    return [d.text for d in load_documents().values()]


def build_subset(schema: object, documents: list[str]) -> str:
    from graphql import (
        GraphQLEnumType,
        GraphQLInputObjectType,
        GraphQLInterfaceType,
        GraphQLObjectType,
        GraphQLScalarType,
        GraphQLUnionType,
        TypeInfo,
        TypeInfoVisitor,
        Visitor,
        get_named_type,
        parse,
        visit,
    )

    used: dict[str, set[str]] = {}
    named: set[str] = set()

    def need_input(t: object) -> None:
        nt = get_named_type(t)
        if nt.name in named:
            return
        named.add(nt.name)
        if isinstance(nt, GraphQLInputObjectType):
            for f in nt.fields.values():
                need_input(f.type)

    info = TypeInfo(schema)

    class Collect(Visitor):
        def enter_field(self, node, *_):                                           # noqa: ANN001
            parent, field = info.get_parent_type(), info.get_field_def()
            if parent is None or field is None or node.name.value.startswith("__"):
                return
            used.setdefault(parent.name, set()).add(node.name.value)
            named.add(parent.name)
            named.add(get_named_type(field.type).name)
            for arg in field.args.values():
                need_input(arg.type)

        def enter_inline_fragment(self, node, *_):                                 # noqa: ANN001
            if node.type_condition:
                named.add(node.type_condition.name.value)

        def enter_variable_definition(self, node, *_):                             # noqa: ANN001
            need_input(schema.get_type(get_named_type_name(node.type)))           # type: ignore[attr-defined]

    def get_named_type_name(t) -> str:                                               # noqa: ANN001
        while hasattr(t, "type"):
            t = t.type
        return str(t.name.value)

    for doc in documents:
        visit(parse(doc), TypeInfoVisitor(info, Collect()))

    # an object implementing a kept interface must carry the interface's kept fields
    for name in list(named):
        t = schema.get_type(name)                                                  # type: ignore[attr-defined]
        if isinstance(t, GraphQLObjectType):
            for iface in t.interfaces:
                if iface.name in named:
                    used.setdefault(name, set()).update(used.get(iface.name, set()))

    def ref(t: object) -> str:
        return str(t)

    out: list[str] = ["# Recorded from the live Atlassian GraphQL gateway by `python -m tools.gql_schema_subset --fetch`. Only what the stored queries use."]
    for name in sorted(named):
        t = schema.get_type(name)                                                  # type: ignore[attr-defined]
        if t is None or name in ("String", "Int", "Float", "Boolean", "ID"):
            continue
        if isinstance(t, GraphQLScalarType):
            out.append(f"scalar {name}")
        elif isinstance(t, GraphQLEnumType):
            out.append(f"enum {name} {{ " + " ".join(t.values) + " }")
        elif isinstance(t, GraphQLInputObjectType):
            out.append(f"input {name} {{\n" + "\n".join(f"  {fn}: {ref(f.type)}" for fn, f in t.fields.items() if get_named_type(f.type).name in named) + "\n}")
        elif isinstance(t, GraphQLUnionType):
            members = [m.name for m in t.types if m.name in named]
            if members:
                out.append(f"union {name} = " + " | ".join(members))
        elif isinstance(t, (GraphQLObjectType, GraphQLInterfaceType)):
            fields = used.get(name, set())
            if not fields:
                fields = {next(iter(t.fields))}                                     # a type only named in a fragment condition: keep one field
                named.update(get_named_type(t.fields[f].type).name for f in fields)
            kw = "interface" if isinstance(t, GraphQLInterfaceType) else "type"
            impl = [i.name for i in t.interfaces if i.name in named]
            lines = []
            for fn in sorted(fields):
                f = t.fields[fn]
                args = ", ".join(f"{an}: {ref(a.type)}" for an, a in f.args.items())
                lines.append(f"  {fn}{'(' + args + ')' if args else ''}: {ref(f.type)}")
            out.append(f"{kw} {name}{' implements ' + ' & '.join(impl) if impl else ''} {{\n" + "\n".join(lines) + "\n}")
    return "\n\n".join(out) + "\n"


def fetch() -> int:
    import httpx
    from graphql import build_client_schema, get_introspection_query

    env = {**_dotenv(), **{k: v for k, v in __import__("os").environ.items() if k.startswith("ATLASSIAN_")}}
    r = httpx.post(f"https://{env['ATLASSIAN_SITE']}/gateway/api/graphql", json={"query": get_introspection_query(descriptions=False)},
                   auth=(env["ATLASSIAN_EMAIL"], env["ATLASSIAN_API_TOKEN"]), timeout=180)
    r.raise_for_status()
    schema = build_client_schema(r.json()["data"])
    SUBSET.parent.mkdir(parents=True, exist_ok=True)
    SUBSET.write_text(build_subset(schema, _documents()), encoding="utf-8")
    print("wrote", SUBSET.relative_to(ROOT))
    return check()


def check() -> int:
    from graphql import build_schema, parse, validate

    schema = build_schema(SUBSET.read_text(encoding="utf-8"))
    bad = 0
    for text in _documents():
        errors = validate(schema, parse(text))
        for e in errors:
            bad += 1
            print("INVALID:", text.split("(")[0].strip(), "-", e.message)
    print("gql schema subset:", f"{bad} problem(s)" if bad else "ok")
    return 1 if bad else 0


def _dotenv() -> dict[str, str]:
    path = ROOT / ".env"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


if __name__ == "__main__":
    sys.exit(fetch() if "--fetch" in sys.argv else check())
