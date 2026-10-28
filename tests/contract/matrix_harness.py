"""Generic matrix harness (design 11): every `<backend>` cell declares operations in `atlian_assiant.matrix`; each is checked two ways.

1. DECLARED == WIRED: the backend's `supported_operations()` contains the row's operations and each one is reachable through a protocol part.
2. SMOKE: a per-row scenario runs against the backend's scenario server and checks normalized output.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from atlian_assiant.matrix import CELLS, row_ops
from atlian_assiant.operations import CATALOG
from atlian_assiant.protocols.base import implementation_of


@dataclass
class Ctx:
    backend: Any
    server: Any

    def impl(self, protocol: type) -> Any:
        found = implementation_of(self.backend, protocol)
        assert found is not None, f"{type(self.backend).__name__} does not implement {protocol.__name__}"
        return found


Scenario = Callable[[Ctx], Awaitable[None]]


def cells_for(backend: str) -> list[str]:
    return [slug for slug, kind in CELLS.get(backend, {}).items() if kind]


def check_declared(ctx: Ctx, backend: str, slug: str, *, partial: dict[str, tuple[str, ...]] | None = None, overrides: dict[str, Any] | None = None) -> None:
    want = declared_operations_for_slug(backend, slug, partial, overrides)
    have = ctx.backend.supported_operations()
    assert want <= have, f"{backend}/{slug}: declared but not supported: {sorted(want - have)}"
    unknown = want - set(CATALOG)
    assert not unknown, f"{backend}/{slug}: unknown operation ids {sorted(unknown)}"


def declared_operations_for_slug(backend: str, slug: str, partial: dict[str, tuple[str, ...]] | None, overrides: dict[str, Any] | None) -> frozenset[str]:
    kind = CELLS[backend][slug]
    if kind == "full":
        return frozenset((overrides or {}).get(slug, row_ops(slug)))
    return frozenset((partial or {}).get(slug, ()))
