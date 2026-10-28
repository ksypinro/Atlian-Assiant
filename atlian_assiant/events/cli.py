"""``python -m atlian_assiant.events.cli methods``: the generated human/machine comparison (CC-06). The full ``atlian-assiant`` CLI arrives in P6."""
from __future__ import annotations

import argparse
import sys

from . import catalog


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="atlian-assiant events")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("methods", help="list every delivery method (machine and human channels)")
    m.add_argument("--implemented", action="store_true", help="only implemented methods")
    m.add_argument("--kind", choices=[k.value for k in catalog.MethodKind])
    args = ap.parse_args(argv)
    methods = [x for x in catalog.DELIVERY_METHODS if (not args.implemented or x.implemented) and (not args.kind or x.kind.value == args.kind)]
    lines = ["| ID | Method | Kind | Ingress | Phase | Status |", "|---|---|---|---|---|---|"]
    lines += [f"| {x.id} | {x.name} | {x.kind.value} | {x.ingress.value} | {x.phase} | {'implemented' if x.implemented else 'planned'} |" for x in methods]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
