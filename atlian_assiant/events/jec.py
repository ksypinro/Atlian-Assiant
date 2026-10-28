"""``python -m atlian_assiant.events.jec --handler pkg.mod:fn``: the script JEC runs for an action mapping (EV section 25)."""
from __future__ import annotations

import argparse
import sys

from .sources.jec import run_jec_handler
from .sources.ssm import load_handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jec", add_help=True)
    ap.add_argument("--handler", required=True, help="package.module:function (async, takes a ChangeEvent)")
    known, rest = ap.parse_known_args(sys.argv[1:] if argv is None else argv)
    try:
        handler = load_handler(known.handler)
    except (ValueError, ImportError, AttributeError) as exc:
        print(f"cannot load handler {known.handler!r}: {exc}", file=sys.stderr)
        return 2
    return run_jec_handler(handler, rest)


if __name__ == "__main__":
    sys.exit(main())
