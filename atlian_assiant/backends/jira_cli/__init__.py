"""JIRA_CLI backend: community ``ankitpokhrel/jira-cli`` (design 10.7). Third party: pinned by ``min_version``; ``describe()`` warns."""
from __future__ import annotations

from typing import ClassVar

from ...capabilities import BackendKind
from ..cli.backend import CliBackend, CliBackendFactory
from ..cli.command_maps import JIRA_CLI_MAP

PARTIAL_OPS: dict[str, tuple[str, ...]] = {
    "jira-comments-list": ("jira.issue.comments.list",),
    "jira-transitions-list": ("jira.issue.transitions.list",),
    "agile-write": ("jira.agile.issue.move_to_sprint",),
}


class JiraCliBackend(CliBackend):
    kind: ClassVar[BackendKind] = BackendKind.JIRA_CLI
    tool: ClassVar[str] = "jira"
    cmap = JIRA_CLI_MAP
    partial = PARTIAL_OPS
    min_version: ClassVar[tuple[int, int, int] | None] = (1, 5, 0)
    version_args: ClassVar[tuple[str, ...]] = ("version",)     # jira-cli has no --version flag
    third_party: ClassVar[bool] = True


class JiraCliBackendFactory(CliBackendFactory):
    kind: ClassVar[BackendKind] = BackendKind.JIRA_CLI
    backend_class = JiraCliBackend
    binary_field = "jira_cli_path"


__all__ = ["PARTIAL_OPS", "JiraCliBackend", "JiraCliBackendFactory"]
