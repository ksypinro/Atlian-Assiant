from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from tools.track import checks, report
from tools.track.engine import LEASE, TrackError, World
from tools.track.model import ROOT, Status, Store, Task, iso, now


@pytest.fixture()
def sandbox(tmp_path: Path) -> World:
    """A tiny independent tracking tree with 4 tasks: A(P0) <- B(P0) <- C(P0), D(P1) human-only."""
    base = tmp_path / "tracking"
    for d in ("definitions/authored", "definitions/generated", "state", "gates", "evidence", ".results"):
        (base / d).mkdir(parents=True)
    for f in ("workstreams.yaml", "seed_hints.yaml"):
        shutil.copy(ROOT / "tracking/definitions" / f, base / "definitions" / f)
    phases = {"phases": [{"id": p, "name": p, "scope": "s", "exit_criteria": "e", "machine_checks": ["true"], "human_items": []}
                         for p in ("P0", "P1", "P2", "P3", "P4", "P4b", "P4c", "P5", "P6")]}
    (base / "definitions/phases.yaml").write_text(yaml.safe_dump(phases))

    def mk(id, deps=(), phase="P0", checks_=("true",), **kw):
        t = Task(id=id, title=id, phase=phase, workstream="CORE", depends_on=list(deps), checks=list(checks_), deliverables=["src/"], **kw)
        (base / "definitions/authored" / f"{id}.yaml").write_text(yaml.safe_dump(t.model_dump(mode="json")))

    mk("A"); mk("B", ["A"]); mk("C", ["B"], checks_=("false",)); mk("D", phase="P1", human_only=True); mk("A2")
    return World.load(Store(tmp_path))


def test_ready_order_and_deps(sandbox: World) -> None:
    assert [t.id for t in sandbox.ready_tasks()] == ["A", "A2"]
    assert sandbox.weight()["A"] == 2


def test_full_lifecycle(sandbox: World) -> None:
    sandbox.claim("A", "bot")
    with pytest.raises(TrackError, match="WIP"):
        sandbox.claim("A2", "bot")
    s, _ = sandbox.submit("A", "bot", "abc123")
    assert s.status is Status.IN_REVIEW and (sandbox.root / s.evidence).exists()
    sandbox.review("A", "alice", approve=False, note="fix")
    assert sandbox.status("A") is Status.CHANGES_REQUESTED
    sandbox.submit("A", "bot")
    sandbox.review("A", "alice", approve=True)
    assert sandbox.status("A") is Status.DONE
    assert [t.id for t in sandbox.ready_tasks()] == ["B", "A2"]


def test_submit_refused_when_check_fails(sandbox: World) -> None:
    for tid in ("A", "B"):
        sandbox.claim(tid, "bot"); sandbox.submit(tid, "bot"); sandbox.review(tid, "r", True)
    sandbox.claim("C", "bot")
    with pytest.raises(TrackError, match="checks failed"):
        sandbox.submit("C", "bot")
    assert sandbox.status("C") is Status.CLAIMED


def test_lease_expiry_releases(sandbox: World) -> None:
    s = sandbox.claim("A", "bot")
    s.lease_expires = iso(now() - timedelta(minutes=1))
    sandbox.store.save_state(s)
    again = World.load(sandbox.store)
    assert again.status("A") is Status.TODO and again.states["A"].claimed_by is None


def test_human_only_never_offered_or_claimable(sandbox: World) -> None:
    assert "D" not in [t.id for t in sandbox.ready_tasks()]
    with pytest.raises(TrackError):
        sandbox.claim("D", "bot")
    sandbox.review("D", "alice", True)
    assert sandbox.status("D") is Status.DONE


def test_phase_window_and_gate(sandbox: World) -> None:
    # P2 task is beyond the window (first unsigned gate P0 -> max active P1)
    t = Task(id="E", title="E", phase="P2", workstream="CORE")
    (sandbox.store.authored / "E.yaml").write_text(yaml.safe_dump(t.model_dump(mode="json")))
    w = World.load(sandbox.store)
    assert "E" not in [x.id for x in w.ready_tasks()]
    with pytest.raises(TrackError, match="not done"):
        w.sign_gate("P0", "alice")


def test_block_and_unblock(sandbox: World) -> None:
    sandbox.claim("A", "bot")
    sandbox.block("A", "bot", "credentials", "need token")
    assert sandbox.status("A") is Status.BLOCKED
    sandbox.unblock("A", "alice")
    assert sandbox.status("A") is Status.TODO


def test_needs_gate_requires_environment(sandbox: World) -> None:
    t = Task(id="N", title="N", phase="P0", workstream="CORE", needs="sandbox")
    (sandbox.store.authored / "N.yaml").write_text(yaml.safe_dump(t.model_dump(mode="json")))
    assert "N" not in [x.id for x in World.load(sandbox.store).ready_tasks()]
    (sandbox.store.base / "environment.yaml").write_text("available: [sandbox]\n")
    assert "N" in [x.id for x in World.load(sandbox.store).ready_tasks()]


def test_regression_detection(sandbox: World) -> None:
    sandbox.claim("A", "bot"); sandbox.submit("A", "bot"); sandbox.review("A", "r", True)
    t = sandbox.tasks["A"].model_copy(update={"checks": ["false"]})
    (sandbox.store.authored / "A.yaml").write_text(yaml.safe_dump(t.model_dump(mode="json")))
    w = World.load(sandbox.store)
    assert w.verify_done() == ["A"]
    assert World.load(sandbox.store).regressed("A")


def test_report_changes_with_state_and_is_deterministic(sandbox: World) -> None:
    before = report.render(sandbox)
    assert report.render(sandbox) == before
    sandbox.claim("A", "bot")
    assert report.render(World.load(sandbox.store)) != before
