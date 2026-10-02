"""The real repository's tracking tree must always be consistent with the design document."""
from __future__ import annotations

from tools.track import checks, report
from tools.track.engine import World
from tools.track.seed_design import build_all


def test_definitions_valid() -> None:
    assert checks.validate() == []


def test_design_fully_covered() -> None:
    assert checks.check_coverage() == []


def test_seed_counts_match_design_inventory() -> None:
    ids = [t.id for t in build_all()]
    assert len({i for i in ids if i.startswith("DM-")} - {i for i in ids if i.endswith((".P", ".C"))}) >= 2  # guards DM-45, DM-49
    assert sum(i.startswith("CC-") for i in ids) == 15
    assert sum(i.startswith("VER-") for i in ids) == 27
    assert sum(i.startswith("BE.") for i in ids) == 12
    assert {f"DM-{n:02d}" for n in range(1, 50)} == {i.split(".")[0] for i in ids if i.startswith("DM-")}


def test_dashboard_is_current() -> None:
    assert report.check(World.load())
