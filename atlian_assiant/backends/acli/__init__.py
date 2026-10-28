"""ACLI backend: the official Atlassian CLI (design 10.5)."""
from __future__ import annotations

from typing import ClassVar

from ...capabilities import BackendKind
from ..cli.backend import CliBackend, CliBackendFactory
from ..cli.command_maps import ACLI_MAP

#: ◐ rows: the subset acli 1.3.39 exposes (design section 11, DCR-004)
PARTIAL_OPS: dict[str, tuple[str, ...]] = {
    "jira-attachment-download": ("jira.issue.attachments.list",),
    "jira-comment-worklog-add": ("jira.issue.comment.add",),
    "jira-links-write": ("jira.issue.link.create",),
    "jira-projects": ("jira.project.list", "jira.project.get"),
    "jira-fields-meta": ("jira.meta.link_types.list",),
    "agile-read": ("jira.agile.board.list", "jira.agile.board.get", "jira.agile.sprint.list", "jira.agile.sprint.get"),
}


class AcliBackend(CliBackend):
    kind: ClassVar[BackendKind] = BackendKind.ACLI
    tool: ClassVar[str] = "acli"
    cmap = ACLI_MAP
    partial = PARTIAL_OPS


class AcliBackendFactory(CliBackendFactory):
    kind: ClassVar[BackendKind] = BackendKind.ACLI
    backend_class = AcliBackend
    binary_field = "acli_path"


__all__ = ["PARTIAL_OPS", "AcliBackend", "AcliBackendFactory"]
