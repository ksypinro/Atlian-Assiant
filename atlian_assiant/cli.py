"""``atlian-assiant``: the command-line interface for humans, CI and shell-based agents (design 16.8).

Every command goes through the same client, policy and routing as the Python API. Output is Markdown for people and ``--json`` for programs; errors print the
message and its hint on stderr with a non-zero exit code (1 = Atlassian error, 2 = usage).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio

from .agent.tools import _jsonable, render_result
from .core.errors import AtlassianError

ClientFactoryFn = Callable[[argparse.Namespace], Any]


def _csv(value: str | None) -> list[str] | None:
    return [v for v in (value or "").split(",") if v] or None


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="atlian-assiant", description="Jira and Confluence from one client: every access method, one policy.")
    ap.add_argument("--profile", help="named profile from ~/.config/atlian-assiant/profiles.toml (default: ATLASSIAN_* environment)")
    ap.add_argument("--preset", choices=["service", "agent", "shell", "analytics", "data_center"], help="backend preference preset (default: shell)")
    ap.add_argument("--access", help="comma-separated access classes to allow (default: read,search): read,search,write,delete,manage,analytics,org_admin")
    ap.add_argument("--yes", action="store_true", help="approve confirmations non-interactively (writes still need --access write)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("describe", help="what is configured, which backends are ready and why not")

    jira = sub.add_parser("jira", help="Jira").add_subparsers(dest="sub", required=True)
    issue = jira.add_parser("issue", help="issues").add_subparsers(dest="action", required=True)
    g = issue.add_parser("get", help="fetch one issue")
    g.add_argument("key")
    g.add_argument("--fields", help="comma-separated fields")
    s = issue.add_parser("search", help="search with JQL")
    s.add_argument("jql")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--fields", help="comma-separated fields")
    c = issue.add_parser("count", help="count issues matching JQL")
    c.add_argument("jql")

    conf = sub.add_parser("confluence", help="Confluence").add_subparsers(dest="sub", required=True)
    cs = conf.add_parser("search", help="search with CQL")
    cs.add_argument("cql")
    cs.add_argument("--limit", type=int, default=20)
    cg = conf.add_parser("page", help="fetch one page")
    cg.add_argument("content_id")

    ev = sub.add_parser("events", help="event and notification delivery methods").add_subparsers(dest="sub", required=True)
    m = ev.add_parser("methods", help="list every delivery method")
    m.add_argument("--implemented", action="store_true")
    m.add_argument("--kind")
    r = ev.add_parser("recommend", help="recommend delivery methods for your network constraints")
    r.add_argument("--products", default="jira,confluence")
    r.add_argument("--no-ingress", action="store_true", help="outbound-only: no public endpoint")
    r.add_argument("--aws", action="store_true")
    r.add_argument("--azure", action="store_true")
    r.add_argument("--gcp", action="store_true")
    r.add_argument("--cloudflare", action="store_true")

    wh = sub.add_parser("webhooks", help="webhooks").add_subparsers(dest="sub", required=True)
    wj = wh.add_parser("jira", help="Jira admin webhooks").add_subparsers(dest="action", required=True)
    we = wj.add_parser("ensure", help="create or update the admin webhook for a URL (idempotent)")
    we.add_argument("--url", required=True)
    we.add_argument("--events", required=True, help="comma-separated, e.g. jira:issue_updated")
    we.add_argument("--name", default="atlian-assiant")
    we.add_argument("--jql")
    wj.add_parser("list", help="list admin webhooks")

    fg = sub.add_parser("forge", help="Forge companion app").add_subparsers(dest="sub", required=True)
    fs = fg.add_parser("scaffold", help="write a deployable event-inbox Forge app")
    fs.add_argument("--products", default="jira,confluence")
    fs.add_argument("--mode", choices=["inbox", "native", "remote"], default="inbox")
    fs.add_argument("--out", required=True, type=Path)
    fs.add_argument("--remote-endpoint")

    tl = sub.add_parser("tools", help="LLM tool definitions").add_subparsers(dest="sub", required=True)
    te = tl.add_parser("export", help="export tool definitions for an LLM API")
    te.add_argument("--format", choices=["anthropic", "openai", "mcp", "json_schema"], default="anthropic")
    tl.add_parser("list", help="list the tools the policy allows")
    ti = tl.add_parser("invoke", help="run one tool")
    ti.add_argument("name")
    ti.add_argument("--args", default="{}", help="JSON arguments")

    op = sub.add_parser("operations", help="the full operation index").add_subparsers(dest="sub", required=True)
    od = op.add_parser("discover", help="search operations by intent")
    od.add_argument("query")
    od.add_argument("--product")
    od.add_argument("--access")
    od.add_argument("--limit", type=int, default=10)
    oc = op.add_parser("call", help="call an operation by id")
    oc.add_argument("operation")
    oc.add_argument("--args", default="{}", help="JSON arguments")
    oc.add_argument("--dry-run", action="store_true")

    mcp = sub.add_parser("mcp", help="MCP server mode").add_subparsers(dest="sub", required=True)
    ms = mcp.add_parser("serve", help="serve every allowed tool over MCP")
    ms.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    ms.add_argument("--host", default="127.0.0.1")
    ms.add_argument("--port", type=int, default=8765)
    ms.add_argument("--auth-token", help="bearer token for the http transport (or ATLASSIAN_MCP_TOKEN)")
    return ap


def default_client(args: argparse.Namespace) -> Any:
    from .capabilities import Access
    from .config import AtlassianConfig
    from .factory import ClientFactory
    from .policy import AccessPolicy, CallbackConfirmation

    config = AtlassianConfig.from_profile(args.profile) if args.profile else AtlassianConfig()
    access = _csv(args.access)
    policy = AccessPolicy(allowed=frozenset(Access(a) for a in access), allow_credit_consuming=config.allow_credit_consuming) if access else AccessPolicy.from_config(config)
    return ClientFactory().build_async(config, preset=args.preset or "shell", policy=policy, confirm=_confirmer(args, CallbackConfirmation))


def _confirmer(args: argparse.Namespace, callback_cls: Any) -> Any:
    def ask(plan: Any) -> bool:
        if args.yes:
            return True
        if not sys.stdin.isatty():
            print(f"refusing: {plan.summary} needs confirmation and stdin is not a terminal (pass --yes to approve)", file=sys.stderr)
            return False
        return input(f"{plan.summary}\nProceed? [y/N] ").strip().lower() in ("y", "yes")
    return callback_cls(ask)


def _emit(args: argparse.Namespace, value: Any) -> None:
    if args.json:
        print(json.dumps(_jsonable(value), indent=2, ensure_ascii=False, default=str))
    else:
        print(render_result(value))


async def run(args: argparse.Namespace, client: Any) -> int:
    cmd = args.cmd
    if cmd == "describe":
        _emit(argparse.Namespace(json=True), client.describe())
    elif cmd == "jira" and args.action == "get":
        _emit(args, await client.jira.issues.get(args.key, fields=_csv(args.fields)))
    elif cmd == "jira" and args.action == "search":
        _emit(args, await client.jira.issues.search(args.jql, fields=_csv(args.fields) or ("summary", "status", "assignee", "updated"), limit=args.limit))
    elif cmd == "jira" and args.action == "count":
        _emit(args, {"count": await client.jira.issues.count(args.jql)})
    elif cmd == "confluence" and args.sub == "search":
        _emit(args, await client.confluence.search.cql(args.cql, limit=args.limit))
    elif cmd == "confluence" and args.sub == "page":
        _emit(args, await client.confluence.content.get(args.content_id))
    elif cmd == "events" and args.sub == "methods":
        from .events import cli as events_cli

        return events_cli.main(["methods", *(["--implemented"] if args.implemented else []), *(["--kind", args.kind] if args.kind else [])])
    elif cmd == "events" and args.sub == "recommend":
        clouds = [c for c in ("aws", "azure", "gcp", "cloudflare") if getattr(args, c)]
        _emit(args, await client.events.recommend_plan(args.products.split(","), max_ingress="none" if args.no_ingress else "managed", clouds=clouds or None))
    elif cmd == "webhooks" and args.action == "list":
        _emit(args, await client.webhooks.list_admin())
    elif cmd == "webhooks" and args.action == "ensure":
        _emit(args, await ensure_webhook(client, args))
    elif cmd == "forge":
        _emit(args, scaffold_forge(args))
    elif cmd == "tools" and args.sub == "export":
        print(json.dumps(client.tools.export(args.format), indent=2))
    elif cmd == "tools" and args.sub == "list":
        _emit(args, [{"name": s.name, "when_to_use": s.when_to_use} for s in client.tools.list()])
    elif cmd == "tools" and args.sub == "invoke":
        result = await client.tools.ainvoke(args.name, json.loads(args.args))
        print(json.dumps(result.model_dump(mode="json"), indent=2, default=str) if args.json else result.text)
        return 0 if result.ok else 1
    elif cmd == "operations" and args.sub == "discover":
        _emit(args, await client.index.discover(args.query, product=args.product, access=args.access, limit=args.limit))
    elif cmd == "operations" and args.sub == "call":
        _emit(args, await client.index.call(args.operation, json.loads(args.args), dry_run=args.dry_run))
    elif cmd == "mcp":
        import os

        from .agent import mcp_server

        token = args.auth_token or os.environ.get("ATLASSIAN_MCP_TOKEN")
        if args.transport == "stdio":
            await mcp_server.serve_stdio(client)
        else:
            await mcp_server.serve_http(client, host=args.host, port=args.port, token=token)
    return 0


async def ensure_webhook(client: Any, args: argparse.Namespace) -> Any:
    """Idempotent: update the admin webhook that already points at the URL, otherwise create it."""
    events = args.events.split(",")
    for hook in await client.webhooks.list_admin():
        if hook.url == args.url:
            return await client.webhooks.update_admin(hook.id, args.name, args.url, events, jql=args.jql)
    return await client.webhooks.register_admin(args.name, args.url, events, jql=args.jql)


def scaffold_forge(args: argparse.Namespace) -> dict[str, Any]:
    from .backends.forge.generator import ForgeAppSpec, generate

    products = tuple({"jsm": "jira-service-management", "jira_software": "jira-software"}.get(p, p) for p in args.products.split(","))
    files = generate(ForgeAppSpec(mode=args.mode, products=products, remote_endpoint=args.remote_endpoint))
    out = Path(args.out)
    for rel, text in files.items():
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return {"written": sorted(files), "directory": str(out), "next": "forge register, then forge deploy -e production and forge install"}


def main(argv: list[str] | None = None, *, client_factory: ClientFactoryFn | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "forge":                                    # needs no credentials
            _emit(args, scaffold_forge(args))
            return 0
        if args.cmd == "events" and args.sub == "methods":
            from .events import cli as events_cli

            return events_cli.main(["methods", *(["--implemented"] if args.implemented else []), *(["--kind", args.kind] if args.kind else [])])
        client = (client_factory or default_client)(args)
        return int(anyio.run(run, args, client))
    except AtlassianError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"hint: {exc.hint}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"error: --args is not valid JSON ({exc})", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
