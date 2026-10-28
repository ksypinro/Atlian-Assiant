"""CapabilityProbe (design 10.5): ``<cli> --version`` and ``<cli> <area> --help`` once, cached; mapped operations whose flags are missing are dropped."""
from __future__ import annotations

import re

from ...core.cli_runner import CliRunner
from ...core.errors import BackendUnavailableError, CliExecutionError
from .commandmap import CommandMap, ProbeReport

_VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def parse_version(text: str) -> tuple[int, int, int] | None:
    m = _VERSION.search(text)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def _names_command(help_text: str, prefix: tuple[str, ...]) -> bool:
    return not prefix or " ".join(prefix) in help_text


def _advertises(help_text: str, flag: str) -> bool:
    """``--list`` must not match ``--list-sprints`` (or ``--limit``): whole-flag match only."""
    return re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", help_text) is not None


class CapabilityProbe:
    def __init__(self, runner: CliRunner, cmap: CommandMap, *, min_version: tuple[int, int, int] | None = None, version_args: tuple[str, ...] = ("--version",)) -> None:
        self.runner, self.cmap, self.min_version, self._version_args = runner, cmap, min_version, version_args
        self._report: ProbeReport | None = None

    async def probe(self, *, refresh: bool = False) -> ProbeReport:
        if self._report is not None and not refresh:
            return self._report
        ver = await self.runner.run(self._version_args)
        raw = (ver.stdout or ver.stderr).strip()
        version = parse_version(raw)
        if self.min_version and (version is None or version < self.min_version):
            need = ".".join(map(str, self.min_version))
            raise BackendUnavailableError(f"{self.runner.label} {raw or 'unknown version'} is older than the minimum {need}",
                                          hint=f"Upgrade {self.runner.label} to {need} or newer.")
        helps: dict[tuple[str, ...], str | None] = {}
        available: set[str] = set()
        dropped: dict[str, str] = {}
        for cmd in self.cmap.all:
            op = cmd.op
            prefix = cmd.prefix
            if prefix not in helps:
                try:
                    helps[prefix] = (await self.runner.run([*prefix, "--help"])).stdout
                except CliExecutionError:
                    helps[prefix] = None
            text = helps[prefix]
            if op in dropped:
                continue
            if text is None or not _names_command(text, prefix):
                # some CLIs (acli) answer an unknown subcommand with the PARENT's help and exit 0: the usage line must name the full command
                dropped[op] = f"`{self.runner.label} {' '.join(prefix)} --help` failed: the command does not exist in this version"
                continue
            missing = [f for f in cmd.requires if not _advertises(text, f)]
            if missing:
                dropped[op] = f"flag(s) {', '.join(missing)} not advertised by `{' '.join(prefix)} --help`"
            else:
                available.add(op)
        available -= set(dropped)                       # an operation is only available when EVERY command it needs is
        self._report = ProbeReport(".".join(map(str, version)) if version else None, frozenset(available), dropped)
        return self._report
