"""Documentation tooling. P0 provides the docstring lint; P1 adds AGENT_GUIDE.md / llms.txt generation.

Lint rules (design sections 13.2 and 16.5): every public facade method has a summary line plus the sections
``When to use:``, ``Args:``, ``Returns:``, ``Raises:``, ``Access:`` and ``Example:``; the Args section documents every
parameter; examples are executed against a FakeBackend-backed client (calls with a valid shape that hit missing data
are accepted; wrong signatures fail).
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import sys
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

REQUIRED = ("When to use:", "Args:", "Returns:", "Raises:", "Access:", "Example:")


@dataclass(frozen=True)
class LintIssue:
    where: str
    problem: str

    def __str__(self) -> str:
        return f"{self.where}: {self.problem}"


def public_methods(namespace: Any) -> list[tuple[str, Callable[..., Any]]]:
    out = []
    for name, member in inspect.getmembers(type(namespace)):
        if name.startswith("_"):
            continue
        if inspect.iscoroutinefunction(member) or inspect.isasyncgenfunction(member):
            out.append((name, getattr(namespace, name)))
    return out


def lint_docstring(where: str, fn: Callable[..., Any]) -> list[LintIssue]:
    doc = inspect.getdoc(fn)
    if not doc:
        return [LintIssue(where, "missing docstring")]
    issues = []
    first = doc.strip().splitlines()[0].strip()
    if not first or first.endswith(":"):
        issues.append(LintIssue(where, "first line must be a one-sentence summary"))
    for section in REQUIRED:
        if section not in doc:
            issues.append(LintIssue(where, f"missing section {section!r}"))
    if "Args:" in doc:
        args_block = doc.split("Args:", 1)[1]
        for end in REQUIRED[2:]:
            if end in args_block:
                args_block = args_block.split(end, 1)[0]
        for p in inspect.signature(fn).parameters:
            if p in ("self", "backend"):
                continue
            if f"{p}:" not in args_block:
                issues.append(LintIssue(where, f"parameter {p!r} is not documented under Args"))
        if "backend" in inspect.signature(fn).parameters and "backend:" not in args_block:
            issues.append(LintIssue(where, "parameter 'backend' is not documented under Args"))
    return issues


def examples(fn: Callable[..., Any]) -> list[str]:
    doc = inspect.getdoc(fn) or ""
    if "Example:" not in doc:
        return []
    block = doc.split("Example:", 1)[1]
    return [line.strip()[4:].strip() for line in block.splitlines() if line.strip().startswith(">>>")]


async def run_example(client: Any, source: str) -> str | None:
    """Run one example with ``client`` bound. Returns an error string for failures that indicate a wrong example."""
    from atlassian_client.core.errors import AtlassianError

    body = textwrap.indent(source if "await " in source or "async for" in source else source, "    ")
    code = f"async def __example(client):\n    _r = None\n{textwrap.indent('_r = ' + source if not _is_statement(source) else source, '    ')}\n    return _r\n"
    del body
    ns: dict[str, Any] = {}
    try:
        exec(compile(code, "<example>", "exec"), ns)  # noqa: S102 - docstring examples we ship ourselves
        await ns["__example"](client)
    except AtlassianError:
        return None                                  # valid call shape; the fixture simply lacks that data
    except Exception as exc:                         # TypeError, ValidationError, AttributeError ... = a wrong example
        return f"{type(exc).__name__}: {exc}"
    return None


def _is_statement(source: str) -> bool:
    try:
        ast.parse(source, mode="eval")
        return False
    except SyntaxError:
        return True


def lint_namespace_tree(client: Any) -> list[LintIssue]:
    from atlassian_client.agent.describe import walk_namespaces

    issues: list[LintIssue] = []
    for path, ns in walk_namespaces(client):
        for name, method in public_methods(ns):
            issues += lint_docstring(f"{path}.{name}", method)
    return issues


async def run_all_examples(client: Any) -> list[LintIssue]:
    from atlassian_client.agent.describe import walk_namespaces

    issues: list[LintIssue] = []
    for path, ns in walk_namespaces(client):
        for name, method in public_methods(ns):
            for src in examples(method):
                err = await run_example(client, src)
                if err:
                    issues.append(LintIssue(f"{path}.{name}", f"example failed: {src!r} -> {err}"))
    return issues


def build_fake_client() -> Any:
    from atlassian_client import AtlassianConfig, BackendKind, ClientFactory
    from atlassian_client.testing import FakeBackend

    cfg = AtlassianConfig(site="example.atlassian.net")
    return ClientFactory().build_async(cfg, backends=[], extra_backends={BackendKind.REST: FakeBackend()})


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    client = build_fake_client()
    issues = lint_namespace_tree(client)
    if "--examples" in args or "--lint" in args or not args:
        issues += asyncio.run(run_all_examples(client))
    for i in issues:
        print(i)
    print("docgen lint:", f"{len(issues)} problem(s)" if issues else "ok")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
