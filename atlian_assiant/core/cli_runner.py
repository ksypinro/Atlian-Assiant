"""CliRunner: runs one third-party CLI (acli, twg, jira) with a timeout, an env allow-list and stderr mapped to ``CliExecutionError`` (design 10.5).

The binary is always invoked with an argv list (never a shell). Only allow-listed environment variables reach the child, so a CLI cannot read
unrelated secrets from this process. An injectable ``executor`` lets tests (and fake binaries) replace process creation.
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import anyio

from .errors import (
    AuthenticationError,
    BackendUnavailableError,
    CliExecutionError,
    NotFoundError,
    PermissionDeniedError,
)

DEFAULT_ENV_ALLOW = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "SYSTEMROOT")


@dataclass(frozen=True)
class CliResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


Executor = Callable[[Sequence[str], str | None, float, Mapping[str, str]], Awaitable[CliResult]]


async def _anyio_executor(argv: Sequence[str], stdin: str | None, timeout: float, env: Mapping[str, str]) -> CliResult:
    with anyio.fail_after(timeout):
        proc = await anyio.run_process(list(argv), input=stdin.encode() if stdin is not None else None, env=dict(env), check=False)
    return CliResult(tuple(argv), proc.returncode, proc.stdout.decode(errors="replace"), proc.stderr.decode(errors="replace"))


@dataclass
class CliRunner:
    binary: str
    timeout: float = 60.0
    env_allow: Sequence[str] = DEFAULT_ENV_ALLOW
    extra_env: Mapping[str, str] = field(default_factory=dict)
    executor: Executor = _anyio_executor
    which: Callable[[str], str | None] = shutil.which
    name: str | None = None

    @property
    def label(self) -> str:
        return self.name or os.path.basename(self.binary)

    def available(self) -> bool:
        return self.which(self.binary) is not None

    def _env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k in self.env_allow}
        env.update(self.extra_env)
        return env

    async def run(self, args: Sequence[str], *, stdin: str | None = None, timeout: float | None = None, check: bool = True) -> CliResult:
        """Run ``binary args``; with ``check`` a non-zero exit raises ``CliExecutionError`` (or a more specific error when stderr says why)."""
        if not self.available():
            raise BackendUnavailableError(f"the {self.label} CLI ({self.binary}) is not installed or not on PATH",
                                          hint=f"Install {self.label}, run its one-time login as a human, then retry.")
        argv = (self.binary, *map(str, args))
        try:
            result = await self.executor(argv, stdin, timeout or self.timeout, self._env())
        except TimeoutError as exc:
            raise CliExecutionError(f"`{self.label} {' '.join(args[:3])} ...` timed out after {timeout or self.timeout:g}s",
                                    hint="Raise the CLI timeout or narrow the request.", retryable=True) from exc
        if check and result.returncode != 0:
            raise map_cli_failure(self.label, result)
        return result


def map_cli_failure(label: str, result: CliResult) -> Exception:
    """stderr -> the closest typed error. The CLI's own message is kept (truncated); it never contains our credentials."""
    text = (result.stderr.strip() or result.stdout.strip())[:500]
    low = text.lower()
    shown = " ".join(result.argv[1:4])
    kw: dict[str, Any] = {"operation": None}
    if any(w in low for w in ("not logged in", "unauthorized", "authentication", "login required", "401")):
        return AuthenticationError(f"{label} is not authenticated: {text}", hint=f"A human must run `{label}` login once; the client never types credentials.", **kw)
    if any(w in low for w in ("not found", "does not exist", "404")):
        return NotFoundError(f"{label}: {text}", hint="Not found OR you lack permission to see it. Verify the key/id by searching.", **kw)
    if any(w in low for w in ("forbidden", "permission", "403")):
        return PermissionDeniedError(f"{label} was denied: {text}", hint="The signed-in user lacks permission for this action.", **kw)
    return CliExecutionError(f"`{label} {shown} ...` failed ({result.returncode}): {text}",
                             hint="Run the same command in a terminal to see the full output; check the CLI version and login.")
