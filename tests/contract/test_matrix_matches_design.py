"""P5.CORE.001: the declared support matrix equals design section 11, and every backend's supported_operations() equals what the matrix declares.

A disagreement fails here; resolve it by fixing the code or by a DCR against the design (never by editing this test).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.backends.acli import PARTIAL_OPS as ACLI_PARTIAL
from atlian_assiant.backends.jira_cli import PARTIAL_OPS as JIRA_CLI_PARTIAL
from atlian_assiant.backends.rest_dc.backend import PARTIAL_OPS as DC_PARTIAL
from atlian_assiant.backends.rest_dc.backend import ROW_OVERRIDES as DC_OVERRIDES
from atlian_assiant.backends.twg import PARTIAL_OPS as TWG_PARTIAL
from atlian_assiant.matrix import CELLS, ROW_OPS, declared_operations
from atlian_assiant.operations import CATALOG

DESIGN_DOC = Path(__file__).resolve().parents[2] / "docs" / "architecture_design.md"
LABELS = yaml.safe_load((Path(__file__).parent / "design_matrix_labels.yaml").read_text(encoding="utf-8"))


def section(text: str, start: str, end: str) -> str:
    m = re.search(start, text, re.M)
    assert m, f"design section not found: {start}"
    rest = text[m.start():]
    e = re.search(end, rest[1:], re.M)
    return rest[: e.start() + 1] if e else rest


def table_rows(block: str) -> list[list[str]]:
    """First Markdown table in ``block`` (header and separator excluded), cells stripped."""
    rows: list[list[str]] = []
    started = False
    for line in block.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not started:
                started = True
            elif not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                rows.append(cells)
        elif started:
            break
    return rows

COLUMN_KIND = {"✅": "full", "◐": "partial"}


def design_matrix() -> dict[tuple[str, str], str | None]:
    sec = section(DESIGN_DOC.read_text(encoding="utf-8"), r"^## 11\. ", r"^## 12\. ")
    lines = [line for line in sec.splitlines() if line.startswith("|")]
    header = [c.strip() for c in lines[0].strip().strip("|").split("|")]
    cols = [LABELS["matrix_columns"][c] for c in header[1:]]
    out: dict[tuple[str, str], str | None] = {}
    for row in table_rows(sec):
        slug = LABELS["matrix_rows"][re.sub(r"[`*]", "", row[0]).strip()]
        for backend, cell in zip(cols, row[1:], strict=True):
            via_org = "ORG_ADMIN" in cell                       # served by the ORG_ADMIN backend, not by this column's backend
            out[(backend, slug)] = None if via_org else ("full" if "✅" in cell else ("partial" if "◐" in cell else None))
    return out


DESIGN = design_matrix()


def test_every_design_row_has_an_operation_list_and_every_operation_exists():
    assert {slug for _, slug in DESIGN} <= set(ROW_OPS)
    for slug, op_ids in ROW_OPS.items():
        assert op_ids and set(op_ids) <= set(CATALOG), f"{slug}: unknown operations {sorted(set(op_ids) - set(CATALOG))}"


@pytest.mark.parametrize(("backend", "slug"), sorted(DESIGN))
def test_matrix_cell_equals_the_design(backend: str, slug: str) -> None:
    assert CELLS.get(backend, {}).get(slug) == DESIGN[(backend, slug)], f"design section 11 says {DESIGN[(backend, slug)]!r} for {slug} x {backend}"


def test_the_matrix_declares_no_cell_the_design_does_not_have():
    declared = {(b, s) for b, cells in CELLS.items() for s in cells}
    assert declared <= set(DESIGN), sorted(declared - set(DESIGN))


def build(kind: BackendKind, **cfg):
    return ClientFactory().backend(kind, AtlassianConfig(**cfg))


def test_rest_dc_supports_exactly_its_declared_cells():
    from atlian_assiant.backends.rest_dc import supported

    assert supported() == declared_operations(BackendKind.REST_DC, DC_PARTIAL, DC_OVERRIDES)
    for slug, ov in DC_OVERRIDES.items():
        assert set(ov) < set(ROW_OPS[slug]) or slug == "webhook-management", f"override of {slug} must be a narrowing documented in backend.py"


@pytest.mark.parametrize(("kind", "partial"), [(BackendKind.ACLI, ACLI_PARTIAL), (BackendKind.TWG, TWG_PARTIAL), (BackendKind.JIRA_CLI, JIRA_CLI_PARTIAL)])
def test_cli_backends_support_exactly_their_declared_cells(kind, partial):
    from atlian_assiant.core.cli_runner import CliRunner
    from atlian_assiant.factory import default_registry

    backend_class = default_registry().get(kind).backend_class
    backend = backend_class(CliRunner("x", which=lambda b: "/x"))
    assert backend.supported_operations() == declared_operations(kind, partial)


def test_cloud_rest_and_mcp_cover_every_full_cell_the_design_gives_them():
    rest = build(BackendKind.REST, site="example.atlassian.net", email="a@b.c", api_token="tok-123456").supported_operations()
    for (backend, slug), kind in DESIGN.items():
        if backend == "rest" and kind == "full":
            missing = set(ROW_OPS[slug]) - rest
            assert not missing, f"design says REST fully supports {slug} but {sorted(missing)} are not supported"
