"""Drive every ready, non-human task of a phase through claim -> submit -> approve (delegated reviewer).

Usage: python scripts/drive_phase.py P0 --agent claude --reviewer claude [--limit N]
Stops on the first failing submit and prints the failure; human-only tasks are never touched.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.track import report  # noqa: E402
from tools.track.engine import TrackError, World  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase")
    ap.add_argument("--agent", default="claude")
    ap.add_argument("--reviewer", default="claude")
    ap.add_argument("--limit", type=int, default=1000)
    a = ap.parse_args()
    done = 0
    while done < a.limit:
        w = World.load()
        ready = [t for t in w.ready_tasks() if t.phase == a.phase]
        if not ready:
            break
        t = ready[0]
        try:
            w.claim(t.id, a.agent)
            w.submit(t.id, a.agent)
            World.load().review(t.id, a.reviewer, True, "delegated P0 approval: checks passed")
        except TrackError as e:
            print(f"STOP at {t.id}:\n{e}")
            return 1
        done += 1
        print(f"approved {t.id}")
    report.write(World.load())
    print(f"{done} task(s) approved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
