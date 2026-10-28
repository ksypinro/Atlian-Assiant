"""The public API surface snapshot (docs/RELEASING.md): removals are breaking, additions need a snapshot refresh.

    python -m tools.api_surface --write   # record the current surface
    python -m tools.api_surface --check   # exit 1 when the surface lost something (breaking) or gained something not yet recorded
"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "docs" / "api_surface.json"
sys.path.insert(0, str(ROOT))


def current() -> dict[str, list[str]]:
    import atlian_assiant
    from atlian_assiant import operations, protocols
    from atlian_assiant.agent.tools import CURATED

    extras = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]
    from tools import codegen

    parser = __import__("atlian_assiant.cli", fromlist=["build_parser"]).build_parser()
    commands = sorted(parser._subparsers._group_actions[0].choices)           # type: ignore[union-attr]  # noqa: SLF001 - argparse has no public accessor
    return {"top_level": sorted(atlian_assiant.__all__), "protocols": sorted(protocols.__all__), "operations": sorted(operations.CATALOG),
            "tools": sorted(s.name for s in CURATED), "extras": sorted(extras), "cli_commands": commands, "generated_apis": sorted(a for a, _ in codegen.APIS.values())}


def compare(old: dict[str, list[str]], new: dict[str, list[str]]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    removed = {k: sorted(set(old.get(k, [])) - set(new.get(k, []))) for k in old}
    added = {k: sorted(set(new.get(k, [])) - set(old.get(k, []))) for k in new}
    return {k: v for k, v in removed.items() if v}, {k: v for k, v in added.items() if v}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    now = current()
    if "--write" in args:
        SNAPSHOT.write_text(json.dumps(now, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print("wrote", SNAPSHOT.relative_to(ROOT))
        return 0
    old: dict[str, Any] = json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else {}
    removed, added = compare(old, now)
    if removed:
        print("BREAKING: the public API lost", json.dumps(removed, indent=1), "(deprecate first, or bump the MAJOR version and run --write)")
    if added:
        print("NEW public API not in the snapshot", json.dumps(added, indent=1), "(bump MINOR and run `python -m tools.api_surface --write`)")
    if not removed and not added:
        print("api surface: ok")
    return 1 if removed or added else 0


if __name__ == "__main__":
    sys.exit(main())
