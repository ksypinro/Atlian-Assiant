"""Backends: adapters that implement protocol subsets (design section 10)."""
from __future__ import annotations

from ..factory import BackendFactory


def builtin_factories() -> list[BackendFactory]:
    from .mcp import McpBackendFactory
    from .rest_cloud import RestBackendFactory

    return [RestBackendFactory(), McpBackendFactory()]
