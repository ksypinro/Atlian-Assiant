"""`track validate` and `track check-coverage`."""
from __future__ import annotations

import re
from pathlib import Path

from .model import DESIGN, PHASES, ROOT, Store
from .seed_design import build_all, design_headings, hints, read_design


def research_sections() -> dict[str, set[str]]:
    out = {}
    for key, name in (("DA", "atlassian_jira_confluence_data_access_research.md"), ("EV", "atlassian_webhook_event_stream_private_delivery_research.md")):
        p = ROOT / name
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        out[key] = set(re.findall(r"^#{1,2} (\d+)\. ", text, re.M)) | set(re.findall(r"^#{2,4} (\d+\.\d+) ", text, re.M))
    return out


def validate(store: Store | None = None) -> list[str]:
    store = store or Store()
    errs: list[str] = []
    try:
        tasks = store.load_tasks()
    except Exception as e:  # schema/dup errors
        return [f"definitions invalid: {e}"]
    ws = store.load_workstreams()
    phases = store.load_phases()
    states = store.load_all_states()
    for t in tasks.values():
        if t.phase not in PHASES or t.phase not in phases:
            errs.append(f"{t.id}: unknown phase {t.phase}")
        if t.workstream not in ws:
            errs.append(f"{t.id}: unknown workstream {t.workstream}")
        for d in t.depends_on:
            if d not in tasks:
                errs.append(f"{t.id}: depends_on unknown task {d}")
        if t.id in t.depends_on:
            errs.append(f"{t.id}: depends on itself")
    for sid in states:
        if sid not in tasks:
            errs.append(f"state/{sid}.yaml has no task definition")
    # cycles
    color: dict[str, int] = {}

    def visit(n: str, path: list[str]) -> None:
        if color.get(n) == 2 or n not in tasks:
            return
        if color.get(n) == 1:
            errs.append("dependency cycle: " + " -> ".join(path[path.index(n):] + [n]))
            return
        color[n] = 1
        for d in tasks[n].depends_on:
            visit(d, path + [n])
        color[n] = 2

    for tid in tasks:
        visit(tid, [])
    # a task may not depend on a later phase
    for t in tasks.values():
        for d in t.depends_on:
            if d in tasks and PHASES.index(tasks[d].phase) > PHASES.index(t.phase):
                errs.append(f"{t.id} ({t.phase}) depends on later-phase task {d} ({tasks[d].phase})")
    return errs


def check_coverage(store: Store | None = None) -> list[str]:
    store = store or Store()
    errs: list[str] = []
    tasks = store.load_tasks()
    text = read_design()
    # 1. generated tasks equal what the design currently produces (detects stale/missing/hand-deleted)
    expected = {t.id: t for t in build_all(store)}
    on_disk = {tid: t for tid, t in tasks.items() if t.source.startswith("design:")}
    for tid in sorted(set(expected) - set(on_disk)):
        errs.append(f"design element has no task: {tid} (run `track seed`)")
    for tid in sorted(set(on_disk) - set(expected)):
        errs.append(f"task {tid} no longer matches the design (run `track seed`)")
    for tid in set(expected) & set(on_disk):
        if expected[tid] != on_disk[tid]:
            errs.append(f"generated task {tid} differs from the design (stale or hand-edited; run `track seed`)")
    # 2. sentinel counts so a silent parser failure cannot pass
    dm = {re.match(r"DM-(\d+)", t)[1] for t in expected if t.startswith("DM-")}
    if sorted(map(int, dm)) != list(range(1, 50)):
        errs.append("DM ledger is not DM-01..DM-49")
    for prefix, n in (("CC-", 15), ("VER-", 27), ("BE.", 12), ("GATE.", 9)):
        got = len([t for t in expected if t.startswith(prefix)])
        if got != n:
            errs.append(f"expected {n} {prefix}* tasks from the design, parsed {got}")
    rows = hints(store)["matrix_rows"]
    seen_rows = {t.split(".", 2)[2] for t in expected if t.startswith("MX.")}
    for label, spec in rows.items():
        if spec["slug"] not in seen_rows:
            errs.append(f"matrix row '{label}' produced no cells")
    # 3. every implementable design section is referenced by at least one task
    refs = {r for t in tasks.values() for r in t.design_refs}
    heads = design_headings(text)
    top = {h for h in heads if "." not in h}
    needed = {f"§{n}" for n in list(range(4, 21)) + [22]}
    for sec in sorted(needed & top, key=lambda s: int(s[1:])):
        if not any(r == sec or r.startswith(sec + ".") for r in refs):
            errs.append(f"design section {sec} is not referenced by any task")
    # 4. refs must exist
    for t in tasks.values():
        for r in t.design_refs:
            if r not in heads:
                errs.append(f"{t.id}: design ref {r} does not exist in the design")
    rs = research_sections()
    for t in tasks.values():
        for r in t.research_refs:
            m = re.match(r"(DA|EV) §(\d+(?:\.\d+)?)", r)
            if not m or (rs[m[1]] and m[2] not in rs[m[1]]):
                errs.append(f"{t.id}: research ref '{r}' not found")
    # 5. every EV research section is mapped by some DM/CC task
    ev_mapped = {re.match(r"EV §(\d+)", r)[1] for t in tasks.values() for r in t.research_refs if r.startswith("EV")}
    for sec in sorted(rs["EV"], key=lambda s: float(s)):
        if "." not in sec and sec not in ev_mapped:
            errs.append(f"event research section EV §{sec} is not mapped to any task")
    return errs
