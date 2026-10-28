"""TWG backend: the Teamwork Graph CLI (design 10.6)."""
from __future__ import annotations

from typing import ClassVar

from ...capabilities import BackendKind, Deployment
from ..cli.backend import CliBackend, CliBackendFactory
from ..cli.command_maps import TWG_MAP

#: ◐ rows: twg serves Confluence reads, Rovo search and Teamwork Graph context only (design section 11, DCR-006)
PARTIAL_OPS: dict[str, tuple[str, ...]] = {
    "conf-content-get": ("confluence.content.get", "confluence.space.list", "confluence.space.get"),     # content list needs a space in twg: REST
    "graph-context": ("graph.context",),
}


class TwgBackend(CliBackend):
    kind: ClassVar[BackendKind] = BackendKind.TWG
    tool: ClassVar[str] = "twg"
    cmap = TWG_MAP
    partial = PARTIAL_OPS


class TwgBackendFactory(CliBackendFactory):
    kind: ClassVar[BackendKind] = BackendKind.TWG
    deployments = frozenset({Deployment.CLOUD})                     # Teamwork Graph is a Cloud product
    backend_class = TwgBackend
    binary_field = "twg_path"


__all__ = ["PARTIAL_OPS", "TwgBackend", "TwgBackendFactory"]
