"""CliTransport (design 10.5): runs a mapped command for the operation a REST component asked for and returns the REST-shaped JSON."""
from __future__ import annotations

import json
import mimetypes
import re
import tempfile
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any

import httpx

from ...core.cli_runner import CliRunner
from ...core.errors import CliExecutionError, UnsupportedOperationError
from ...core.http import RequestSpec
from ..optransport import Handler, OpTransport
from .commandmap import Command, CommandMap, shape


class CliTransport(OpTransport):
    def __init__(self, tool: str, runner: CliRunner, cmap: CommandMap, *, site: str | None = None, ops: Iterable[str] | None = None, hint: str | None = None) -> None:
        wanted = None if ops is None else frozenset(ops)
        handlers: dict[str, Handler] = {op: self._handler(op) for op in cmap.operations if wanted is None or op in wanted}
        super().__init__(tool, handlers, site=site, hint=hint or f"The {tool} CLI does not expose this; the router falls back to REST or MCP.")
        self.tool, self.runner, self.cmap = tool, runner, cmap
        self._downloads: dict[str, bytes] = {}
        self.prepare: Callable[[], Awaitable[None]] | None = None

    async def send(self, spec: RequestSpec) -> httpx.Response:
        if self.prepare is not None:
            await self.prepare()                  # CapabilityProbe once; may drop this operation (-> Unsupported, the router falls back)
        return await super().send(spec)

    def drop(self, ops: Iterable[str]) -> None:
        for op in ops:
            self.handlers.pop(op, None)

    def _handler(self, op: str) -> Handler:
        async def handle(spec: RequestSpec) -> Any:
            cmd = self.cmap.select(op, spec.path)
            if cmd is None:
                raise UnsupportedOperationError(f"{op}: no {self.tool} command for {spec.path}", operation=op, backend=self.tool,
                                                hint="This request shape is not available through the CLI backend; the router falls back.")
            return await self._run(cmd, spec)
        return handle

    def _parse(self, cmd: Command, stdout: str) -> Any:
        text = stdout.strip()
        if not text:
            return None
        try:
            data = json.loads(text)
            if self.cmap.envelope and isinstance(data, dict) and "apiVersion" in data and self.cmap.envelope in data:
                return data[self.cmap.envelope]
            return data
        except json.JSONDecodeError as exc:
            raise CliExecutionError(f"{self.tool} printed non-JSON output for {cmd.op}: {text[:120]!r}",
                                    hint=f"Check that the installed {self.tool} supports machine-readable output for this command (see the command map notes).",
                                    operation=cmd.op, backend=self.tool) from exc

    async def _run(self, cmd: Command, spec: RequestSpec) -> Any:
        if cmd.kind == "download":
            return await self._download(cmd, spec)
        if cmd.kind == "upload":
            return await self._upload(cmd, spec)
        if cmd.files:
            result = await self._run_with_files(cmd, spec)
        else:
            argv, stdin = self.cmap.render(cmd, spec)
            result = await self.runner.run(argv, stdin=stdin)
        if cmd.kind == "csv":
            return result.stdout.encode()
        if cmd.kind == "count":                                   # e.g. acli --count prints "Number of work items in the search: 12"
            numbers = re.findall(r"\d+", result.stdout)
            if not numbers:
                raise CliExecutionError(f"{self.tool} printed no count for {cmd.op}: {result.stdout.strip()[:120]!r}", operation=cmd.op, backend=self.tool)
            return {"count": int(numbers[-1])}
        return shape(cmd.wrap, self._parse(cmd, result.stdout), spec)

    async def _run_with_files(self, cmd: Command, spec: RequestSpec) -> Any:
        """Long text (descriptions, comment bodies) goes in a private temp file, never in argv where `ps` shows it to other local users."""
        values = self.cmap.variables(cmd, spec)
        with tempfile.TemporaryDirectory(prefix="atl-cli-") as tmp:          # created 0700: only this user can read the files
            extra = {}
            for name in cmd.files:
                if name in values:
                    path = Path(tmp) / f"{name}.txt"
                    path.write_text(values[name], encoding="utf-8")
                    extra[name] = str(path)
            argv, stdin = self.cmap.render(cmd, spec, extra)
            return await self.runner.run(argv, stdin=stdin)

    async def _download(self, cmd: Command, spec: RequestSpec) -> Any:
        ident = spec.path.rsplit("/", 1)[-1]
        if spec.path.startswith("/attachment/content/"):                   # second request of download_attachment: the bytes fetched by the first
            return self._downloads.pop(ident, b"")
        with tempfile.TemporaryDirectory(prefix="atl-cli-") as tmp:
            argv, _ = self.cmap.render(cmd, spec, {"outdir": tmp})
            await self.runner.run(argv)
            files = sorted(p for p in Path(tmp).iterdir() if p.is_file())
            if not files:
                raise CliExecutionError(f"{self.tool} did not write an attachment file for {ident}", hint="Check the attachment id and that the CLI is logged in.",
                                        operation=cmd.op, backend=self.tool)
            data = files[0].read_bytes()
            self._downloads[ident] = data
            return {"id": ident, "filename": files[0].name, "size": len(data), "mimeType": mimetypes.guess_type(files[0].name)[0] or "application/octet-stream"}

    async def _upload(self, cmd: Command, spec: RequestSpec) -> Any:
        name, data = next(iter((spec.files or {}).values()))[:2]
        with tempfile.TemporaryDirectory(prefix="atl-cli-") as tmp:
            path = Path(tmp) / Path(str(name)).name                         # never trust a caller-supplied directory part
            path.write_bytes(data if isinstance(data, bytes) else bytes(data))
            argv, _ = self.cmap.render(cmd, spec, {"file": str(path)})
            result = await self.runner.run(argv)
        out = self._parse(cmd, result.stdout)
        return out if isinstance(out, list) else [out or {"filename": str(name), "size": len(data)}]
