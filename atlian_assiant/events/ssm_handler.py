"""``python -m atlian_assiant.events.ssm_handler --handler pkg.mod:fn --payload '<json>'``: the command an SSM document runs (EV section 24)."""
from __future__ import annotations

import argparse
import sys

import anyio

from .sources.ssm import SsmDocumentAdapter, load_handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ssm_handler", description="Run a handler for a Jira Automation event delivered by AWS SSM")
    ap.add_argument("--handler", required=True, help="package.module:function (async, takes a ChangeEvent)")
    ap.add_argument("--payload", required=True, help="JSON envelope from Jira Automation")
    args = ap.parse_args(argv)
    try:
        handler = load_handler(args.handler)
    except (ValueError, ImportError, AttributeError) as exc:
        print(f"cannot load handler {args.handler!r}: {exc}", file=sys.stderr)
        return 2
    return anyio.run(SsmDocumentAdapter(handler).run, args.payload)


if __name__ == "__main__":
    sys.exit(main())
