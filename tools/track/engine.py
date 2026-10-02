"""Scheduling and state transitions. All mutations of state go through this module."""
from __future__ import annotations

import fnmatch
import os
import subprocess
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import yaml

from .model import (PHASES, ROOT, Blocked, HistoryEntry, Review, State, Status, Store, Task, TaskType, iso, now,
                    parse_iso, phase_index)

LEASE = timedelta(hours=4)
SATISFIED = {Status.DONE, Status.WONT_DO}
ACTIVE = {Status.CLAIMED, Status.IN_REVIEW, Status.CHANGES_REQUESTED, Status.BLOCKED}


class TrackError(Exception):
    pass


@dataclass
class World:
    store: Store
    tasks: dict[str, Task]
    states: dict[str, State]
    phases: dict

    @classmethod
    def load(cls, store: Store | None = None) -> "World":
        store = store or Store()
        tasks = store.load_tasks()
        states = {tid: store.load_state(tid) for tid in tasks}
        w = cls(store, tasks, states, store.load_phases())
        w.release_expired()
        return w

    # ---- derived ----
    def status(self, tid: str) -> Status:
        return self.states[tid].status

    def release_expired(self) -> list[str]:
        released = []
        for s in self.states.values():
            if s.status is Status.CLAIMED and s.lease_expires and parse_iso(s.lease_expires) < now():
                self._log(s, "system", "lease_expired", f"was held by {s.claimed_by}")
                s.status, s.claimed_by, s.lease_expires = Status.TODO, None, None
                self.store.save_state(s)
                released.append(s.id)
        return released

    def environment(self) -> set[str]:
        p = self.store.base / "environment.yaml"
        if not p.exists():
            return set()
        return set((yaml.safe_load(p.read_text()) or {}).get("available", []))

    def signed(self, phase: str) -> bool:
        g = self.store.load_gate(phase)
        return bool(g and g.get("signed"))

    def max_active_phase(self) -> int:
        """Index of the first unsigned gate, plus one (work on the next phase may start early)."""
        for i, p in enumerate(PHASES):
            if not self.signed(p):
                return min(i + 1, len(PHASES) - 1)
        return len(PHASES) - 1

    def is_ready(self, t: Task) -> bool:
        if t.type is TaskType.GATE or t.human_only:
            return False
        if self.status(t.id) not in (Status.TODO, Status.CHANGES_REQUESTED):
            return False
        if t.needs != "none" and t.needs not in self.environment():
            return False
        if phase_index(t.phase) > self.max_active_phase():
            return False
        return all(d in self.states and self.states[d].status in SATISFIED for d in t.depends_on)

    def _children(self) -> dict[str, list[str]]:
        ch: dict[str, list[str]] = {tid: [] for tid in self.tasks}
        for t in self.tasks.values():
            for d in t.depends_on:
                if d in ch:
                    ch[d].append(t.id)
        return ch

    def weight(self) -> dict[str, int]:
        """Longest chain of dependents below each task (critical-path weight)."""
        ch, memo = self._children(), {}

        def depth(tid: str) -> int:
            if tid not in memo:
                memo[tid] = 0  # cycle guard; validate() reports cycles separately
                memo[tid] = 1 + max((depth(c) for c in ch[tid]), default=0)
            return memo[tid]

        return {tid: depth(tid) - 1 for tid in self.tasks}

    def ready_tasks(self) -> list[Task]:
        w = self.weight()
        return sorted((t for t in self.tasks.values() if self.is_ready(t)),
                      key=lambda t: (phase_index(t.phase), -w[t.id], t.size != "S", t.id))

    # ---- logging ----
    def _log(self, s: State, by: str, event: str, note: str = "") -> None:
        s.history.append(HistoryEntry(at=iso(now()), by=by, event=event, note=note))

    def _task(self, tid: str) -> Task:
        if tid not in self.tasks:
            raise TrackError(f"unknown task {tid}")
        return self.tasks[tid]

    # ---- agent transitions ----
    def claim(self, tid: str, agent: str) -> State:
        t, s = self._task(tid), self.states[tid]
        if t.human_only:
            raise TrackError(f"{tid} is human_only")
        if not self.is_ready(t):
            raise TrackError(f"{tid} is not ready (status={s.status}, deps/phase/needs unmet)")
        busy = [x.id for x in self.states.values() if x.claimed_by == agent and x.status in (Status.CLAIMED, Status.CHANGES_REQUESTED)]
        if busy:
            raise TrackError(f"WIP limit 1: {agent} already holds {busy[0]}")
        s.status, s.claimed_by, s.lease_expires = Status.CLAIMED, agent, iso(now() + LEASE)
        self._log(s, agent, "claimed")
        self.store.save_state(s)
        return s

    def release(self, tid: str, agent: str) -> None:
        s = self.states[tid]
        if s.claimed_by != agent:
            raise TrackError(f"{tid} not held by {agent}")
        s.status, s.claimed_by, s.lease_expires = Status.TODO, None, None
        self._log(s, agent, "released")
        self.store.save_state(s)

    @property
    def root(self) -> Path:
        return self.store.base.parent

    def run_checks(self, t: Task) -> list[dict]:
        env = dict(os.environ)
        venv = ROOT / ".venv" / "bin"
        if venv.exists():
            env["PATH"] = f"{venv}:{env['PATH']}"
        results = []
        for cmd in t.checks:
            p = subprocess.run(cmd, shell=True, cwd=self.root, env=env, capture_output=True, text=True)
            out = (p.stdout + p.stderr).strip().splitlines()
            results.append({"cmd": cmd, "code": p.returncode, "tail": "\n".join(out[-15:])})
        return results

    def changed_files(self) -> list[str]:
        try:
            a = subprocess.run(["git", "status", "--porcelain"], cwd=self.root, capture_output=True, text=True).stdout
        except OSError:
            return []
        return [l[3:] for l in a.splitlines()]

    def out_of_scope(self, t: Task) -> list[str]:
        allowed = [d.split(" ")[0].rstrip("/") for d in t.deliverables] + ["tracking/state/", "tracking/evidence/", "TRACKING.md"]
        bad = []
        for f in self.changed_files():
            if not any(f == a or f.startswith(a + "/") or fnmatch.fnmatch(f, a) for a in allowed if a):
                bad.append(f)
        return bad

    def submit(self, tid: str, agent: str, commit: str | None = None) -> tuple[State, list[dict]]:
        t, s = self._task(tid), self.states[tid]
        if s.claimed_by != agent or s.status not in (Status.CLAIMED, Status.CHANGES_REQUESTED):
            raise TrackError(f"{tid} is not claimed by {agent}")
        results = self.run_checks(t)
        failed = [r for r in results if r["code"] != 0]
        if failed:
            raise TrackError("checks failed; not submitted:\n" + "\n".join(f"  $ {r['cmd']} -> {r['code']}\n{r['tail']}" for r in failed))
        scope = self.out_of_scope(t)
        ev = self.store.evidence_dir / f"{tid}.md"
        ev.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# Evidence: {tid} - {t.title}", "", f"- submitted by: {agent}", f"- at: {iso(now())}", f"- commit: {commit or 'n/a'}", "",
                 "## Checks", ""]
        for r in results:
            lines += [f"### `{r['cmd']}` -> exit {r['code']}", "```", r["tail"] or "(no output)", "```", ""]
        lines += ["## Files changed", ""] + [f"- {f}" for f in self.changed_files()] + [""]
        if scope:
            lines += ["## Outside declared deliverables (reviewer: check)", ""] + [f"- {f}" for f in scope] + [""]
        ev.write_text("\n".join(lines), encoding="utf-8")
        s.status, s.lease_expires, s.evidence = Status.IN_REVIEW, None, str(ev.relative_to(self.root))
        if commit:
            s.commits.append(commit)
        self._log(s, agent, "submitted", f"{len(results)} checks passed" + (f"; {len(scope)} out-of-scope files" if scope else ""))
        self.store.save_state(s)
        return s, results

    def block(self, tid: str, agent: str, needs: str, reason: str) -> State:
        s = self.states[tid]
        self._task(tid)
        s.status, s.claimed_by, s.lease_expires = Status.BLOCKED, None, None
        s.blocked = Blocked(reason=reason, needs=needs)
        self._log(s, agent, "blocked", f"{needs}: {reason}")
        self.store.save_state(s)
        return s

    # ---- reviewer transitions ----
    def unblock(self, tid: str, reviewer: str, note: str = "") -> None:
        s = self.states[tid]
        if s.status is not Status.BLOCKED:
            raise TrackError(f"{tid} is not blocked")
        s.status, s.blocked = Status.TODO, None
        self._log(s, reviewer, "unblocked", note)
        self.store.save_state(s)

    def review(self, tid: str, reviewer: str, approve: bool, note: str = "") -> State:
        t, s = self._task(tid), self.states[tid]
        direct_human = t.human_only and s.status in (Status.TODO, Status.CLAIMED)
        if s.status is not Status.IN_REVIEW and not direct_human:
            raise TrackError(f"{tid} is not in_review (status={s.status})")
        if approve:
            s.status, s.claimed_by, s.lease_expires = Status.DONE, None, None
        else:
            s.status = Status.CHANGES_REQUESTED if s.claimed_by or s.evidence else Status.TODO
            s.lease_expires = iso(now() + LEASE) if s.claimed_by else None
        s.review = Review(by=reviewer, at=iso(now()), decision="approved" if approve else "rejected", notes=note)
        self._log(s, reviewer, "approved" if approve else "rejected", note)
        self.store.save_state(s)
        return s

    def wont_do(self, tid: str, reviewer: str, note: str) -> None:
        s = self.states[tid]
        s.status, s.claimed_by = Status.WONT_DO, None
        self._log(s, reviewer, "wont_do", note)
        self.store.save_state(s)

    # ---- gates ----
    def phase_tasks(self, phase: str) -> list[Task]:
        return [t for t in self.tasks.values() if t.phase == phase and t.type is not TaskType.GATE]

    def gate_report(self, phase: str) -> dict:
        tasks = self.phase_tasks(phase)
        done = [t for t in tasks if self.status(t.id) in SATISFIED]
        i = phase_index(phase)
        return {"total": len(tasks), "done": len(done), "all_done": len(done) == len(tasks),
                "previous_signed": i == 0 or self.signed(PHASES[i - 1]),
                "regressed": [t.id for t in tasks if self.regressed(t.id)]}

    def regressed(self, tid: str) -> bool:
        p = self.store.base / ".results" / "regressions.yaml"
        return p.exists() and tid in (yaml.safe_load(p.read_text()) or {}).get("failed", [])

    def sign_gate(self, phase: str, reviewer: str, note: str = "") -> dict:
        rep = self.gate_report(phase)
        problems = []
        if not rep["all_done"]:
            problems.append(f"{rep['total'] - rep['done']} task(s) not done")
        if not rep["previous_signed"]:
            problems.append("previous gate not signed")
        if rep["regressed"]:
            problems.append(f"regressed: {rep['regressed']}")
        checks = []
        ph = self.phases[phase]
        t = Task(id=f"GATE.{phase}", title="gate", phase=phase, workstream="REL", checks=ph.machine_checks, type=TaskType.GATE)
        for r in self.run_checks(t):
            checks.append(r)
            if r["code"] != 0:
                problems.append(f"machine check failed: {r['cmd']}")
        if problems:
            raise TrackError("cannot sign gate " + phase + ":\n  - " + "\n  - ".join(problems))
        data = {"phase": phase, "signed": True, "by": reviewer, "at": iso(now()), "note": note,
                "human_items": ph.human_items, "machine_checks": [{"cmd": c["cmd"], "code": c["code"]} for c in checks]}
        self.store.save_gate(phase, data)
        gs = self.states.get(f"GATE.{phase}")
        if gs is not None:
            gs.status = Status.DONE
            self._log(gs, reviewer, "gate_signed", note)
            self.store.save_state(gs)
        return data

    def verify_done(self) -> list[str]:
        failed = []
        for t in self.tasks.values():
            if self.status(t.id) is Status.DONE and t.checks:
                if any(r["code"] != 0 for r in self.run_checks(t)):
                    failed.append(t.id)
        p = self.store.base / ".results" / "regressions.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump({"failed": failed}), encoding="utf-8")
        return failed
