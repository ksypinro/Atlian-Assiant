"""Coverage enforcement (design 14.19): every research section is mapped; every implemented method has real, importable components."""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from atlian_assiant.events import catalog
from atlian_assiant.events.catalog import CROSS_CUTTING, DELIVERY_METHODS, MethodKind

pytestmark = pytest.mark.layer_12

RESEARCH = Path(__file__).parents[2] / "docs/research/atlassian_webhook_event_stream_private_delivery_research.md"
METHOD_TESTS = Path(__file__).parent / "methods"


def _section(ref: str) -> str:
    match = re.match(r"EV §(\d+)", ref)
    assert match, ref
    return match.group(1)


def research_sections() -> set[str]:
    text = RESEARCH.read_text(encoding="utf-8")
    return set(re.findall(r"^#{1,2} (\d+)\. ", text, re.M))


def test_every_research_section_is_mapped() -> None:
    mapped = {_section(r) for m in DELIVERY_METHODS for r in m.research_refs}
    mapped |= {_section(r) for c in CROSS_CUTTING for r in c.research_refs}
    missing = research_sections() - mapped
    assert not missing, f"unmapped research sections: {sorted(missing, key=int)}"
    assert len(research_sections()) == 63


def test_ledger_is_complete_and_unique() -> None:
    ids = [m.id for m in DELIVERY_METHODS]
    assert ids == [f"DM-{n:02d}" for n in range(1, 50)]
    assert [c.id for c in CROSS_CUTTING] == [f"CC-{n:02d}" for n in range(1, 16)]


def test_ledger_matches_the_design_table() -> None:
    """The catalog and design section 14.2 must agree on phase and ingress for every row."""
    design = (Path(__file__).parents[2] / "docs/architecture_design.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| (DM-\d\d) \|.*\| (Required\*?|None(?: / Required)?|LAN|Managed|Tunnel|n/a|Depends) \| (P\w+) \|$", design, re.M)
    assert len(rows) == 49
    by_id = {m.id: m for m in DELIVERY_METHODS}
    for dm, ingress, phase in rows:
        assert by_id[dm].phase == phase, dm
        if ingress.startswith("Required"):
            assert by_id[dm].ingress is catalog.Ingress.REQUIRED, dm


def test_every_implemented_component_is_importable() -> None:
    implemented = [m for m in DELIVERY_METHODS if m.implemented]
    p3 = {"DM-01", "DM-02", "DM-03", "DM-05", "DM-15", "DM-16", "DM-36", "DM-37", "DM-38"}
    p4 = {f"DM-{n:02d}" for n in (6, 7, 8, 9, 10, 11, 12, 13, 14, 17, 18, 19, 39, 40, 41, 42, 43, 46, 47)}
    p4b = {f"DM-{n:02d}" for n in range(20, 36)}
    p4c = {"DM-04", "DM-44", "DM-45", "DM-48", "DM-49"}
    assert {m.id for m in implemented} == p3 | p4 | p4b | p4c and len(implemented) == 49
    for method in implemented:
        for path in filter(None, (method.provisioner, method.consumer, method.verifier)):
            module, _, attr = path.partition(":")
            assert hasattr(importlib.import_module(module), attr), f"{method.id}: {path}"
        if method.kind is not MethodKind.GUARD:
            assert method.consumer, f"{method.id} has no consumer"
        declared = getattr(catalog.resolve(method.consumer), "method_ids", None) if method.consumer else None
        if declared is not None:                                          # report/artifact consumers (DM-14, DM-10 panel) have no method_ids
            assert method.id in declared, f"{method.id}: consumer does not declare it"
    for rule in CROSS_CUTTING:
        if rule.implemented:
            for path in rule.implemented_by:
                module, _, attr = path.partition(":")
                assert hasattr(importlib.import_module(module), attr), f"{rule.id}: {path}"


def test_pure_consumer_methods_declare_no_provisioner() -> None:
    assert {m.id for m in DELIVERY_METHODS if m.implemented and m.provisioner is None} == {"DM-05", "DM-36", "DM-37", "DM-38", "DM-45", "DM-48", "DM-49"}      # pure consumers and guards


def test_every_implemented_method_has_a_contract_test() -> None:
    for method in DELIVERY_METHODS:
        test_file = METHOD_TESTS / f"test_{method.id.lower().replace('-', '_')}.py"
        if method.implemented:
            assert test_file.exists(), f"missing {test_file.name}"


def test_planned_methods_do_not_claim_components_that_do_not_exist() -> None:
    """A planned entry may name its future component, but must not pretend: nothing planned is marked implemented."""
    for method in DELIVERY_METHODS:
        if not method.implemented:
            assert method.phase not in catalog.IMPLEMENTED_PHASES
    assert catalog.IMPLEMENTED_PHASES == {"P3", "P4", "P4b", "P4c"}


def test_preview_legacy_and_entitlement_flags_follow_the_research() -> None:
    by_id = {m.id: m for m in DELIVERY_METHODS}
    assert by_id["DM-04"].legacy and by_id["DM-07"].preview and by_id["DM-19"].entitlements
    assert by_id["DM-45"].kind is MethodKind.GUARD and by_id["DM-49"].kind is MethodKind.GUARD


def test_methods_table_lists_every_method_with_its_status() -> None:
    table = catalog.methods_table()
    assert table.count("\n| DM-") == 49 and "| DM-03 |" in table and "implemented" in table and "planned" not in table          # every ledger row has landed
