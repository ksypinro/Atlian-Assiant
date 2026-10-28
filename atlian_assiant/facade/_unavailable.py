"""Placeholder for namespaces whose backends are scheduled for a later phase (honest, explicit failure)."""
from __future__ import annotations

from typing import Any

from ..core.errors import UnsupportedOperationError


class Unavailable:
    __namespace__ = False

    def __init__(self, name: str, phase: str) -> None:
        self._name, self._phase = name, phase

    def __getattr__(self, item: str) -> Any:
        if item.startswith("__"):
            raise AttributeError(item)
        raise UnsupportedOperationError(
            f"{self._name} is not implemented yet (planned for phase {self._phase})",
            hint="See docs/operation_coverage.md for the implementation status, or use a curated operation / client.raw instead.")

    def __repr__(self) -> str:
        return f"<{self._name}: planned for {self._phase}>"
