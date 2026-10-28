"""OpenAPI -> generated REST tier (design 10.1, 20: P6).

    python -m tools.codegen --write                 # specs/openapi/*.json -> operations_index.json
    python -m tools.codegen --check                 # the committed index equals what the specs generate (CI)
    python -m tools.codegen --check-reachability    # every spec operation resolves to a callable request (exit 1 otherwise)
    python -m tools.codegen --coverage-report       # curated vs generated coverage, Markdown (--write-report to docs/operation_coverage.md)
    python -m tools.codegen --diff PATH             # added / removed / changed operations versus another index (the drift job's PR body)
    python -m tools.codegen --fetch                 # download the specs listed in specs/sources.json (needs network; the nightly drift job)

``specs/openapi`` ships a SEED SUBSET; the nightly drift job replaces it with the real specs and opens a PR with the diff.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SPECS = ROOT / "specs" / "openapi"
OUT = ROOT / "atlian_assiant" / "backends" / "rest_cloud" / "generated" / "operations_index.json"
REPORT = ROOT / "docs" / "operation_coverage.md"
#: spec file stem -> (api key, path prefix the servers URL / gateway already supplies)
APIS = {"jira-v3": ("jira", "/rest/api/3"), "jira-software-agile": ("agile", "/rest/agile/1.0"), "jira-service-management": ("jsm", "/rest/servicedeskapi"),
        "confluence-v2": ("confluence_v2", "/wiki/api/v2"), "confluence-v1": ("confluence_v1", "/wiki/rest/api")}
METHODS = ("get", "put", "post", "delete", "patch", "head")


def _classify(method: str, path: str) -> str:
    sys.path.insert(0, str(ROOT))
    from atlian_assiant.backends.rest_cloud.generated import classify

    return classify(method, path)


def build_index(specs: Path = SPECS) -> dict[str, Any]:
    ops: list[dict[str, Any]] = []
    for file in sorted(specs.glob("*.json")):
        if file.stem not in APIS:
            raise SystemExit(f"{file.name}: unknown spec; add it to APIS in tools/codegen.py")
        api, prefix = APIS[file.stem]
        doc = json.loads(file.read_text(encoding="utf-8"))
        for path, item in sorted(doc.get("paths", {}).items()):
            common = item.get("parameters", [])
            for method in METHODS:
                op = item.get(method)
                if not op:
                    continue
                if not path.startswith(prefix):
                    raise SystemExit(f"{file.name}: {path} is not under {prefix}")
                rel = path[len(prefix):] or "/"
                params = [(p["name"], p["in"], bool(p.get("required"))) for p in [*common, *op.get("parameters", [])] if p.get("in") in ("path", "query", "header")]
                ops.append({"id": f"api.{api}.{op['operationId']}", "api": api, "operationId": op["operationId"], "method": method.upper(), "path": rel,
                            "summary": op.get("summary", ""), "tags": op.get("tags", []), "params": params, "has_body": "requestBody" in op,
                            "access": _classify(method, rel), "deprecated": bool(op.get("deprecated"))})
    ids = [o["id"] for o in ops]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise SystemExit(f"duplicate operation ids: {sorted(dupes)}")
    return {"generated_by": "tools/codegen.py", "operations": sorted(ops, key=lambda o: o["id"])}


def render(index: dict[str, Any]) -> str:
    return json.dumps(index, indent=1, sort_keys=True) + "\n"


def check_reachability() -> list[str]:
    """Every operation in the index must resolve to a concrete request through the runtime (path placeholders filled, surface known)."""
    sys.path.insert(0, str(ROOT))
    from atlian_assiant.backends.rest_cloud import generated as g

    g.load_index.cache_clear()
    problems: list[str] = []
    spec_ids = {o["id"] for o in build_index()["operations"]}
    index = g.load_index()
    if spec_ids != set(index):
        problems.append(f"index is stale: {len(spec_ids - set(index))} missing, {len(set(index) - spec_ids)} extra (run --write)")
    for op in index.values():
        try:
            path = op.render_path({n: "X" for n, where, _ in op.params if where == "path"})
            if "{" in path or not path.startswith("/"):
                problems.append(f"{op.id}: unresolved path {path}")
            op.operation()
            _ = op.surface
        except Exception as exc:                    # pragma: no cover - reported, never swallowed
            problems.append(f"{op.id}: {type(exc).__name__}: {exc}")
    return problems


def _norm(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path).rstrip("/")


def curated_paths() -> dict[tuple[str, str], str]:
    """(surface key, normalized path) -> curated operation id, scanned from the curated REST components' source (heuristic, used by the coverage report)."""
    found: dict[tuple[str, str], str] = {}
    surf = {"J": "jira", "A": "agile", "S": "jsm", "V2": "confluence_v2", "V1": "confluence_v1"}
    for f in (ROOT / "atlian_assiant" / "backends" / "rest_cloud").glob("*.py"):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"""\b(J|A|S|V2|V1)\b[^\n]*?f?"(/[^"\n]*)"[^\n]*?operation="([\w.]+)\"""", text):
            found[(surf[m.group(1)], _norm(re.sub(r"\{[^}/]*\}", "{}", m.group(2))))] = m.group(3)
    return found


def coverage() -> dict[str, Any]:
    sys.path.insert(0, str(ROOT))
    from atlian_assiant import operations as catalog

    index = build_index()["operations"]
    cur = curated_paths()
    rows: dict[str, dict[str, int]] = {}
    for o in index:
        r = rows.setdefault(o["api"], {"total": 0, "curated": 0})
        r["total"] += 1
        r["curated"] += (o["api"], _norm(o["path"])) in cur
    return {"curated_operations": len(catalog.CATALOG), "generated_operations": len(index), "by_api": rows}


def coverage_markdown() -> str:
    c = coverage()
    lines = ["# Operation coverage (curated vs generated)", "", "<!-- GENERATED by `python -m tools.codegen --write-report` - DO NOT EDIT -->", "",
             f"- Curated catalog operations (typed, documented, tool-ready): **{c['curated_operations']}**",
             f"- Generated operations in the vendored OpenAPI specs: **{c['generated_operations']}** (all reachable through `api.<api>.<operationId>`)", "",
             "| API | Generated | Also curated (path match, heuristic) | Generated only |", "|---|---|---|---|"]
    for api, r in sorted(c["by_api"].items()):
        lines.append(f"| {api} | {r['total']} | {r['curated']} | {r['total'] - r['curated']} |")
    return "\n".join(lines) + "\n"


def diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, list[str]]:
    a = {o["id"]: o for o in json.loads(json.dumps(old["operations"]))}
    b = {o["id"]: o for o in json.loads(json.dumps(new["operations"]))}              # tuples vs lists must not look like drift
    return {"added": sorted(b.keys() - a.keys()), "removed": sorted(a.keys() - b.keys()),
            "changed": sorted(i for i in a.keys() & b.keys() if a[i] != b[i])}


def fetch(sources: Path = ROOT / "specs" / "sources.json") -> list[Path]:
    import httpx

    out = []
    for stem, url in json.loads(sources.read_text()).items():
        resp = httpx.get(url, timeout=60, follow_redirects=True)
        resp.raise_for_status()
        SPECS.mkdir(parents=True, exist_ok=True)
        target = SPECS / f"{stem}.json"
        target.write_text(json.dumps(resp.json(), indent=1), encoding="utf-8")
        out.append(target)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tools.codegen")
    for flag in ("--write", "--check", "--check-reachability", "--coverage-report", "--write-report", "--fetch"):
        ap.add_argument(flag, action="store_true")
    ap.add_argument("--diff", metavar="OLD_INDEX")
    a = ap.parse_args(argv)
    if a.fetch:
        for fetched in fetch():
            print("fetched", fetched.relative_to(ROOT))
    if a.write:
        OUT.write_text(render(build_index()), encoding="utf-8")
        print("wrote", OUT.relative_to(ROOT))
    if a.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != render(build_index()):
            print("STALE: operations_index.json differs from the specs (run `python -m tools.codegen --write`)")
            return 1
        print("codegen check: ok")
    if a.check_reachability:
        problems = check_reachability()
        for problem in problems:
            print("UNREACHABLE:", problem)
        n = len(json.loads(OUT.read_text())["operations"]) if OUT.exists() else 0
        print("reachability:", f"{len(problems)} problem(s)" if problems else f"100% ({n} operations reachable)")
        if problems:
            return 1
    if a.coverage_report or a.write_report:
        text = coverage_markdown()
        if a.write_report:
            REPORT.write_text(text, encoding="utf-8")
            print("wrote", REPORT.relative_to(ROOT))
        else:
            print(text)
    if a.diff:
        d = diff(json.loads(Path(a.diff).read_text()), build_index())
        print(json.dumps(d, indent=1))
        return 1 if any(d.values()) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
