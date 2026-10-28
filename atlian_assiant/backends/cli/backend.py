"""Shared CLI backend plumbing: REST components bound to a ``CliTransport`` (design 10.5-10.7)."""
from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar

from ...capabilities import BackendKind, Deployment
from ...config import AtlassianConfig
from ...core.cli_runner import CliRunner
from ...core.errors import BackendUnavailableError
from ...core.http import RequestSpec
from ...core.urls import ApiSurface
from ...factory import Availability
from ...matrix import declared_operations
from ...models.common import BodyInput
from ...protocols.base import BackendHealth
from ..graph_part import GraphPart
from ..optransport import OpBackend
from ..rest_cloud.agile import AgileRest
from ..rest_cloud.agile_write import AgileWriteRest
from ..rest_cloud.bodies import body_text
from ..rest_cloud.confluence import ConfluenceRest
from ..rest_cloud.confluence_write import ConfluenceWriteRest
from ..rest_cloud.jira import JiraRest
from ..rest_cloud.jira_write import JiraWriteRest
from ..rest_cloud.jsm import JsmRest
from ..rest_cloud.jsm_write import JsmWriteRest
from .commandmap import CommandMap, ProbeReport
from .probe import CapabilityProbe
from .transport import CliTransport


class CliJiraWrite(JiraWriteRest):
    """CLIs take plain/Markdown text for descriptions and comments, not ADF."""

    def _rich(self, body: BodyInput | str) -> Any:
        return body_text(body)


class CliCsvExporter:
    """``JiraExporter`` through the CLI's native ``--csv`` output."""

    def __init__(self, transport: CliTransport) -> None:
        self.transport = transport

    async def export_issues_csv(self, jql: str, *, fields: Sequence[str] | None = None, dest: Path) -> Path:
        spec = RequestSpec("POST", ApiSurface.JIRA_PLATFORM, "/search/jql", json={"jql": jql, **({"fields": list(fields)} if fields else {})}, operation="export.jira.csv")
        data = (await self.transport.send(spec)).content
        text = data.decode("utf-8", errors="replace")
        if not text.strip() or len(list(csv.reader(io.StringIO(text)))) < 1:
            text = ""
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8", newline="")
        return dest


class CliBackend(OpBackend):
    kind: ClassVar[BackendKind]
    tool: ClassVar[str]
    cmap: ClassVar[CommandMap]
    partial: ClassVar[dict[str, tuple[str, ...]]] = {}
    min_version: ClassVar[tuple[int, int, int] | None] = None
    version_args: ClassVar[tuple[str, ...]] = ("--version",)
    third_party: ClassVar[bool] = False

    @property
    def warnings(self) -> tuple[str, ...]:
        out = []
        if self.third_party:
            need = ".".join(map(str, self.min_version)) if self.min_version else "a tested version"
            out.append(f"third-party CLI: pinned to {need} or newer; output formats are not covered by an Atlassian contract")
        out.append("flags in the command map are unverified until the matching VER item is signed (see CapabilityProbe)")
        return tuple(out)

    def __init__(self, runner: CliRunner, *, site: str | None = None) -> None:
        declared = declared_operations(self.kind, self.partial)
        transport = CliTransport(self.tool, runner, self.cmap, ops=declared, site=site)
        self.runner = runner
        self.jira, self.agile, self.jsm = JiraRest(transport), AgileRest(transport), JsmRest(transport)
        self.confluence, self.graph = ConfluenceRest(transport), GraphPart(transport)
        self.jira_write, self.agile_write, self.jsm_write = CliJiraWrite(transport, self.jira), AgileWriteRest(transport), JsmWriteRest(transport)
        self.confluence_write = ConfluenceWriteRest(transport, self.confluence)
        self.exports = CliCsvExporter(transport)
        parts = (self.jira, self.agile, self.jsm, self.confluence, self.graph, self.jira_write, self.agile_write, self.jsm_write, self.confluence_write, self.exports)
        super().__init__(transport, parts, declared)
        self.cli_transport = transport
        self._probe = CapabilityProbe(runner, self.cmap, min_version=self.min_version, version_args=self.version_args)
        self.probe_report: ProbeReport | None = None
        transport.prepare = self._probe_once          # the first real call probes, so a forced via() never runs an unadvertised command

    async def _probe_once(self) -> None:
        if self.probe_report is None:
            await self.probe()

    async def probe(self, *, refresh: bool = False) -> ProbeReport:
        """Run the capability probe once and drop every operation the installed CLI cannot do (design 10.5)."""
        report = await self._probe.probe(refresh=refresh)
        self.cli_transport.drop(set(self.cmap.operations) - report.available)
        have = self.cli_transport.operations
        self._declared = frozenset(o for o in self._declared if o in have or (o in self.COMPOSED and set(self.COMPOSED[o]) <= have))
        self.probe_report = report
        return report

    async def health(self) -> BackendHealth:
        try:
            report = await self.probe()
        except Exception as exc:                                      # health must never raise
            return BackendHealth("unavailable", f"{type(exc).__name__}: {exc}")
        warn = f" ({len(report.dropped)} operations dropped: flags missing)" if report.dropped else ""
        third = "; third-party CLI: version pinned by min_version" if self.third_party else ""
        return BackendHealth("ready", f"{self.tool} {report.version or '?'}{warn}{third}")


class CliBackendFactory:
    kind: ClassVar[BackendKind]
    deployments: ClassVar[frozenset[Deployment]] = frozenset({Deployment.CLOUD, Deployment.DATA_CENTER})
    extra: ClassVar[str | None] = None
    backend_class: ClassVar[type[CliBackend]]
    binary_field: ClassVar[str]

    def __init__(self, *, which: Any = None) -> None:
        self._which = which

    def _runner(self, config: AtlassianConfig) -> CliRunner:
        binary = getattr(config, self.binary_field)
        runner = CliRunner(binary, name=self.backend_class.tool, timeout=config.timeout_seconds)
        if self._which is not None:
            runner.which = self._which
        return runner

    def availability(self, config: AtlassianConfig) -> Availability:
        runner = self._runner(config)
        if not runner.available():
            tool = self.backend_class.tool
            return Availability(False, f"the {tool} CLI ({runner.binary}) is not installed or not on PATH: install it, run its one-time login, or set ATLASSIAN_{self.binary_field.upper()}")
        return Availability(True)

    def create(self, config: AtlassianConfig, core: Any) -> CliBackend:
        runner = self._runner(config)
        if not runner.available():
            raise BackendUnavailableError(f"the {self.backend_class.tool} CLI ({runner.binary}) is not installed", hint="Install it and run its login once as a human.")
        return self.backend_class(runner, site=config.site)
