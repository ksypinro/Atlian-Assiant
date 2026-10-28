"""Operation x backend cells of design section 11 for the adapter backends (CLIs, GraphQL, browser, data lake).

Test ids are ``<backend>-<row_slug>`` so each ``MX.<backend>.<row-slug>`` task selects exactly its own tests with
``-k '<row_slug> and <backend>'``. Each declared operation is proven against the same check the Cloud REST reference passes.
"""
from __future__ import annotations

import pytest

from atlian_assiant.core.errors import UnsupportedOperationError
from atlian_assiant.matrix import CELLS, row_ops

from .matrix_backends import BACKENDS, rest_ctx
from .matrix_harness import declared_operations_for_slug
from .matrix_ops import OP_CHECKS

pytestmark = [pytest.mark.layer_05, pytest.mark.anyio]

PARAMS = [(b, slug) for b, (_, _, rows) in BACKENDS.items() for slug, kind in CELLS.get(b, {}).items() if kind and (rows is None or slug in rows)]
IDS = [f"{b}-{slug.replace('-', '_')}" for b, slug in PARAMS]


def _declared(backend: str, slug: str) -> frozenset[str]:
    return declared_operations_for_slug(backend, slug, BACKENDS[backend][1], None)


@pytest.mark.parametrize(("backend", "slug"), PARAMS, ids=IDS)
async def test_matrix_cell_declares_and_proves_its_operations(backend: str, slug: str) -> None:
    make = BACKENDS[backend][0]
    declared = _declared(backend, slug)
    kind = CELLS[backend][slug]
    assert declared, f"{backend}/{slug} is a {kind} cell but declares no operation"
    assert declared <= set(row_ops(slug)), f"{backend}/{slug}: operations outside the design row: {sorted(declared - set(row_ops(slug)))}"
    if kind == "full":
        assert declared == set(row_ops(slug)), f"{backend}/{slug}: a full cell must declare the whole row"
    assert declared <= make().backend.supported_operations()
    for op in sorted(declared):
        assert op in OP_CHECKS, f"no check for {op}"
        await OP_CHECKS[op](make())                       # fresh state per operation: writes never leak into the next check


@pytest.mark.parametrize(("backend", "slug"), [p for p in PARAMS if CELLS[p[0]][p[1]] == "partial"], ids=[i for p, i in zip(PARAMS, IDS, strict=True) if CELLS[p[0]][p[1]] == "partial"])
async def test_matrix_partial_cell_refuses_the_rest_of_the_row_so_the_router_falls_back(backend: str, slug: str) -> None:
    make = BACKENDS[backend][0]
    ctx = make()
    outside = set(row_ops(slug)) - _declared(backend, slug)
    assert not (outside & ctx.backend.supported_operations())
    for op in sorted(outside):
        if op in OP_CHECKS:
            with pytest.raises(UnsupportedOperationError):
                await OP_CHECKS[op](make())


@pytest.mark.parametrize("op", sorted(OP_CHECKS))
async def test_reference_cloud_rest_passes_every_check(op: str) -> None:
    ctx = rest_ctx()
    if op not in ctx.backend.supported_operations():
        pytest.skip(f"{op} is not a Cloud REST operation (MCP / CLI only)")
    await OP_CHECKS[op](ctx)
