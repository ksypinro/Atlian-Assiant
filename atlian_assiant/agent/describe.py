"""Capability manifest and per-operation help (design section 16.1). Never contains secrets."""
from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

from .. import operations as ops
from ..capabilities import Access

if TYPE_CHECKING:
    from ..facade.client import AsyncAtlassianClient


def walk_namespaces(obj: Any, prefix: str = "", seen: set[int] | None = None) -> list[tuple[str, Any]]:
    """All facade namespace objects below ``obj`` as (dotted path, instance)."""
    seen = seen if seen is not None else set()
    out: list[tuple[str, Any]] = []
    for name, value in vars(obj).items():
        if name.startswith("_") or id(value) in seen:
            continue
        if getattr(value, "__namespace__", False) is True:
            seen.add(id(value))
            path = f"{prefix}.{name}" if prefix else name
            out.append((path, value))
            out += walk_namespaces(value, path, seen)
    return out


def build_manifest(client: AsyncAtlassianClient) -> dict[str, Any]:
    cfg, rep = client.config, client.build_report
    backends: dict[str, str] = {}
    for kind, reason in rep.skipped.items():
        backends[kind.value] = f"unavailable: {reason}"
    for kind, impl in client.router.backends.items():     # built and injected backends win over "skipped" reasons
        backends[kind.value] = f"ready ({len(impl.supported_operations())} operations)"
    notes: list[str] = []
    if not client.policy.allow_credit_consuming:
        notes.append("Rovo/Teamwork Graph tools consume credits and are disabled (allow_credit_consuming=false).")
    if Access.WRITE not in client.policy.allowed:
        notes.append("Write access is not enabled; the client is read-only.")
    from ..events.info import AUTOMATION_WARNING, events_summary

    notes.append(AUTOMATION_WARNING)
    for kind, impl in sorted(client.router.backends.items(), key=lambda kv: kv[0].value):       # a backend can attach caveats the caller must see (third-party, governance, ...)
        notes.extend(f"{kind.value}: {w}" for w in getattr(impl, "warnings", ()))
    if not rep.built and not client.router.backends:
        notes.append("No backend is available. Configure credentials (see backends for the reason each was skipped).")
    return {
        "site": cfg.site,
        "cloud_id": client.router.cloud_id or cfg.cloud_id,
        "deployment": cfg.deployment.value,
        "preset": client.preset.value,
        "access": sorted(a.value for a in client.policy.allowed),
        "backends": dict(sorted(backends.items())),
        "operations_known": len(ops.CATALOG),
        "events": events_summary(),
        "notes": notes,
    }


def help_for(client: AsyncAtlassianClient, name: str) -> str:
    """Documentation for an operation id (``jira.issue.search``) combining the docstring, access class and live availability."""
    op = ops.CATALOG.get(name)
    if op is None:
        near = [k for k in ops.CATALOG if name.split(".")[0] in k][:8]
        return f"Unknown operation {name!r}. Similar: {', '.join(near) or 'none'}. See operations.CATALOG for every id."
    doc = ""
    for path, ns in walk_namespaces(client):
        method = getattr(ns, "_OPS", {}).get(op.id)
        if method:
            doc = f"client.{path}.{method}\n\n" + (inspect.getdoc(getattr(ns, method)) or "")
            break
    serving = [b.kind.value for b in client.router.candidates(op)]
    lines = [f"Operation: {op.id}", "", doc or f"{op.summary} (no curated facade method yet)", "",
             f"Access class: {op.access.name}" + ("; consumes Rovo credits" if op.consumes_credits else "")
             + ("; Cloud only" if op.cloud_only else ""),
             "Backends that can serve it now: " + (", ".join(serving) or "none configured")]
    return "\n".join(lines)
