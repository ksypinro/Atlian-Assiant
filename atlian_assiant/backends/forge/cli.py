"""ForgeCliRunner: wraps the Forge CLI (`forge register / deploy / install / logs / variables set`). ``forge login`` is one-time human setup."""
from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ...core.errors import BackendUnavailableError, CliExecutionError

Runner = Callable[[Sequence[str], Path | None], "CliResult"]


@dataclass(frozen=True)
class CliResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _run(argv: Sequence[str], cwd: Path | None) -> CliResult:
    p = subprocess.run(list(argv), cwd=cwd, capture_output=True, text=True, timeout=900, check=False)   # noqa: S603 - argv list, never a shell
    return CliResult(p.returncode, p.stdout, p.stderr)


class ForgeCliRunner:
    """Every call returns the CLI's stdout and raises ``CliExecutionError`` (with the CLI's own message) on a non-zero exit. Secrets are never echoed."""

    def __init__(self, *, binary: str = "forge", runner: Runner = _run, which: Callable[[str], str | None] = shutil.which) -> None:
        self.binary, self._runner, self._which = binary, runner, which

    def available(self) -> bool:
        return self._which(self.binary) is not None

    def _call(self, args: Sequence[str], cwd: Path | None = None, *, redact: Sequence[str] = ()) -> str:
        if not self.available():
            raise BackendUnavailableError(f"the Forge CLI ({self.binary}) is not installed", hint="npm install -g @forge/cli, then `forge login` once (human setup).")
        result = self._runner([self.binary, *args], cwd)
        if result.returncode != 0:
            shown = " ".join(a if a not in redact else "***" for a in args)
            err = result.stderr.strip() or result.stdout.strip()
            for secret in redact:
                err = err.replace(secret, "***")
            raise CliExecutionError(f"`forge {shown}` failed ({result.returncode}): {err[:500]}", hint="Run the same command in a terminal to see the full output; "
                                    "check `forge login` and that you are inside the app directory.")
        return result.stdout

    def register(self, directory: Path, name: str) -> str:
        return self._call(["register", name], directory)

    def deploy(self, directory: Path, environment: str = "production") -> str:
        return self._call(["deploy", "-e", environment, "--non-interactive"], directory)

    def install(self, directory: Path, *, site: str, product: str, environment: str = "production", upgrade: bool = False) -> str:
        args = ["install", "--site", site, "--product", product, "-e", environment, "--non-interactive"]
        return self._call(args + (["--upgrade"] if upgrade else []), directory)

    def logs(self, directory: Path, *, environment: str = "production", limit: int = 50) -> str:
        return self._call(["logs", "-e", environment, "--limit", str(limit)], directory)

    def variables_set(self, directory: Path, name: str, value: str, *, environment: str = "production", encrypt: bool = True) -> str:
        return self._call(["variables", "set", *(["--encrypt"] if encrypt else []), "-e", environment, name, value], directory, redact=[value])
