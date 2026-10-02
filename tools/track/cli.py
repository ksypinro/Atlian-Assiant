"""`python -m tools.track <command>`"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

import yaml

from . import checks, report
from .engine import TrackError, World
from .model import ROOT, DEFS_AUTHORED, PHASES, Status, Store, Task, TaskType, State
from .seed_design import build_all


def _head(w: World) -> str | None:
    p = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return p.stdout.strip() or None if p.returncode == 0 else None


def cmd_validate(a, w=None) -> int:
    errs = checks.validate()
    for e in errs:
        print("ERROR", e)
    print("validate:", "FAILED" if errs else "ok")
    return 1 if errs else 0


def cmd_coverage(a) -> int:
    errs = checks.check_coverage()
    for e in errs[:80]:
        print("ERROR", e)
    if len(errs) > 80:
        print(f"... and {len(errs) - 80} more")
    print("check-coverage:", f"FAILED ({len(errs)})" if errs else "ok")
    return 1 if errs else 0


def cmd_seed(a) -> int:
    store = Store()
    tasks = build_all(store)
    n, removed = store.write_generated(tasks)
    print(f"seed: wrote {n} generated tasks, removed {removed} stale; authored: {len(list(store.authored.glob('*.yaml')))}")
    return 0


def cmd_schemas(a) -> int:
    d = Store().base / "schema"
    d.mkdir(parents=True, exist_ok=True)
    for name, model in (("task", Task), ("state", State)):
        (d / f"{name}.schema.json").write_text(json.dumps(model.model_json_schema(), indent=2), encoding="utf-8")
    print("schemas written")
    return 0


def cmd_next(a) -> int:
    w = World.load()
    ready = w.ready_tasks()[: a.limit]
    if not ready:
        print("no ready tasks (check `track report`: gates, blocked items, environment)")
        return 1
    for t in ready:
        print(f"{t.id}\t{t.phase}\t{t.size}\t{t.title}")
    return 0


def cmd_show(a) -> int:
    w = World.load()
    t = w._task(a.id)
    s = w.states[a.id]
    print(yaml.safe_dump({"task": t.model_dump(mode="json"), "state": s.model_dump(mode="json", exclude_none=True), "ready": w.is_ready(t)}, sort_keys=False, allow_unicode=True))
    return 0


def cmd_claim(a) -> int:
    w = World.load()
    s = w.claim(a.id, a.agent)
    t = w.tasks[a.id]
    print(f"claimed {a.id} until {s.lease_expires}\n")
    print(f"{t.title}\nrefs: {t.design_refs} {t.research_refs}\ndeliverables: {t.deliverables}\nacceptance:")
    for x in t.acceptance:
        print("  -", x)
    print("checks:", t.checks)
    return 0


def cmd_release(a) -> int:
    World.load().release(a.id, a.agent)
    print("released")
    return 0


def cmd_submit(a) -> int:
    w = World.load()
    s, results = w.submit(a.id, a.agent, a.commit or _head(w))
    print(f"submitted {a.id}: {len(results)} check(s) passed; evidence {s.evidence}")
    return 0


def cmd_block(a) -> int:
    w = World.load()
    w.block(a.id, a.agent, a.needs, a.reason)
    print("blocked")
    return 0


def cmd_unblock(a) -> int:
    World.load().unblock(a.id, a.reviewer, a.note)
    print("unblocked")
    return 0


def cmd_review(a) -> int:
    w = World.load()
    if a.action == "queue":
        for t in w.tasks.values():
            if w.status(t.id) is Status.IN_REVIEW:
                print(f"{t.id}\t{t.title}\t{w.states[t.id].evidence}")
        return 0
    w.review(a.id, a.reviewer, a.action == "approve", a.note)
    print(f"{a.action}d {a.id}")
    return 0


def cmd_wontdo(a) -> int:
    World.load().wont_do(a.id, a.reviewer, a.note)
    return 0


def cmd_blocked(a) -> int:
    w = World.load()
    for s in w.states.values():
        if s.status is Status.BLOCKED and s.blocked:
            print(f"{s.id}\tneeds={s.blocked.needs}\t{s.blocked.reason}")
    return 0


def cmd_gate(a) -> int:
    w = World.load()
    if a.action == "status":
        for p in PHASES:
            print(p, w.gate_report(p), "signed" if w.signed(p) else "open")
        return 0
    data = w.sign_gate(a.phase, a.reviewer, a.note)
    print("signed", data["phase"])
    return 0


def cmd_dcr(a) -> int:
    store = Store()
    existing = [p.stem for p in DEFS_AUTHORED.glob("DCR-*.yaml")]
    if a.action == "file":
        n = max([int(x.split("-")[1]) for x in existing] + [0]) + 1
        t = Task(id=f"DCR-{n:03d}", title=a.title, type=TaskType.DCR, phase=a.phase, workstream="REL", design_refs=a.refs or [],
                 human_only=True, acceptance=[a.reason], needs="decision", source="authored")
        (DEFS_AUTHORED / f"{t.id}.yaml").write_text(yaml.safe_dump(t.model_dump(mode="json"), sort_keys=False, allow_unicode=True), encoding="utf-8")
        print("filed", t.id)
        return 0
    w = World.load()
    w.review(a.id, a.reviewer, True, a.note)
    print("approved", a.id, "- now edit the design document, then run `track seed`")
    return 0


def cmd_report(a) -> int:
    w = World.load()
    if a.check:
        ok = report.check(w)
        print("report:", "up to date" if ok else "STALE (run `python -m tools.track report`)")
        return 0 if ok else 1
    report.write(w)
    print("TRACKING.md written")
    return 0


def cmd_verify_done(a) -> int:
    failed = World.load().verify_done()
    print("verify-done:", f"{len(failed)} regressed: {failed}" if failed else "ok")
    return 1 if failed else 0


def cmd_metric(a) -> int:
    w = World.load()
    real = [t for t in w.tasks.values() if t.type is not TaskType.GATE]
    m = {"total": len(real), "done": sum(1 for t in real if w.status(t.id) in (Status.DONE, Status.WONT_DO)),
         "ready": len(w.ready_tasks())}
    for p in PHASES:
        r = w.gate_report(p)
        m[f"{p}_done"], m[f"{p}_total"] = r["done"], r["total"]
    print(json.dumps(m, indent=2))
    return 0


MUTATING = {"seed", "submit", "claim", "release", "block", "unblock", "review", "wont-do", "gate", "dcr", "verify-done"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="track", description="Implementation tracker")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, **kw):
        p = sub.add_parser(name, **kw)
        p.set_defaults(fn=fn)
        return p

    add("validate", cmd_validate)
    add("check-coverage", cmd_coverage)
    add("seed", cmd_seed)
    add("schemas", cmd_schemas)
    add("metric", cmd_metric)
    add("verify-done", cmd_verify_done)
    add("blocked", cmd_blocked)
    p = add("next", cmd_next); p.add_argument("--agent", default="agent"); p.add_argument("--limit", type=int, default=5)
    p = add("show", cmd_show); p.add_argument("id")
    p = add("claim", cmd_claim); p.add_argument("id"); p.add_argument("--agent", required=True)
    p = add("release", cmd_release); p.add_argument("id"); p.add_argument("--agent", required=True)
    p = add("submit", cmd_submit); p.add_argument("id"); p.add_argument("--agent", required=True); p.add_argument("--commit")
    p = add("block", cmd_block); p.add_argument("id"); p.add_argument("--agent", required=True)
    p.add_argument("--needs", choices=["credentials", "sandbox", "decision"], required=True); p.add_argument("--reason", required=True)
    p = add("unblock", cmd_unblock); p.add_argument("id"); p.add_argument("--reviewer", required=True); p.add_argument("--note", default="")
    p = add("review", cmd_review); p.add_argument("action", choices=["queue", "approve", "reject"]); p.add_argument("id", nargs="?")
    p.add_argument("--reviewer", default="reviewer"); p.add_argument("--note", default="")
    p = add("wont-do", cmd_wontdo); p.add_argument("id"); p.add_argument("--reviewer", required=True); p.add_argument("--note", required=True)
    p = add("gate", cmd_gate); p.add_argument("action", choices=["status", "sign"]); p.add_argument("phase", nargs="?")
    p.add_argument("--reviewer", default="reviewer"); p.add_argument("--note", default="")
    p = add("dcr", cmd_dcr); p.add_argument("action", choices=["file", "approve"]); p.add_argument("id", nargs="?")
    p.add_argument("--title"); p.add_argument("--reason", default=""); p.add_argument("--phase", default="P0")
    p.add_argument("--refs", nargs="*"); p.add_argument("--reviewer", default="reviewer"); p.add_argument("--note", default="")
    p = add("report", cmd_report); p.add_argument("--check", action="store_true")

    args = ap.parse_args(argv)
    try:
        rc = args.fn(args)
        if args.cmd in MUTATING and rc == 0:
            report.write(World.load())
        return rc
    except TrackError as e:
        print("error:", e, file=sys.stderr)
        return 2
