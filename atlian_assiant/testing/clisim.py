"""FakeCli: a stand-in for acli / twg / jira-cli generated from the SAME command map the backend uses (design 18, layer 5).

The "binary" parses argv with the inverse of the command map, replays the REST request it stands for against a ``ScenarioServer`` and prints what a CLI
would print (the bare list / object). So the table is exercised in both directions and every contract assertion about normalized data holds unchanged.
"""
from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..backends.cli.commandmap import CommandMap, unshape
from ..core.cli_runner import CliResult, CliRunner
from ..core.http import HttpTransport, RequestSpec
from .server import ScenarioServer


class FakeCli:
    def __init__(self, cmap: CommandMap, server: ScenarioServer, *, version: str = "2.0.0", missing_flags: Sequence[str] = (), missing_prefixes: Sequence[tuple[str, ...]] = (),
                 text_output: Sequence[str] = ()) -> None:
        from .. import AtlassianConfig, BackendKind, ClientFactory

        self.cmap, self.server, self.version = cmap, server, version
        self.missing_flags, self.missing_prefixes, self.text_output = set(missing_flags), {tuple(p) for p in missing_prefixes}, set(text_output)
        cfg = AtlassianConfig(site="example.atlassian.net", email="ann@example.com", api_token="secret-token-value")
        self._http: HttpTransport = ClientFactory().backend(BackendKind.REST, cfg, http=server.client()).transport  # type: ignore[attr-defined]
        self.calls: list[tuple[str, ...]] = []

    # ---------------------------------------------------------------------------------------------------- binary behaviour
    def help_text(self, prefix: tuple[str, ...]) -> str | None:
        cmds = [c for c in self.cmap.all if c.prefix == prefix]
        if not cmds or prefix in self.missing_prefixes:
            return None
        flags = {t if isinstance(t, str) else t[0] for c in cmds for t in c.argv if (t if isinstance(t, str) else t[0]).startswith("-")}
        return f"Usage: {' '.join(prefix)} [flags]\n" + "\n".join(f"  {f}" for f in sorted(flags - self.missing_flags))

    async def executor(self, argv: Sequence[str], stdin: str | None, timeout: float, env: Mapping[str, str]) -> CliResult:
        args = tuple(argv[1:])
        self.calls.append(args)
        if args == ("--version",) or args == ("version",):
            return CliResult(tuple(argv), 0, f"tool version {self.version}\n")
        if args and args[-1] == "--help":
            text = self.help_text(args[:-1])
            return CliResult(tuple(argv), 0, text) if text is not None else CliResult(tuple(argv), 1, "", "unknown command")
        parsed = self.cmap.parse(self._inline_files(args), stdin)
        if parsed is None:
            return CliResult(tuple(argv), 2, "", f"unknown command or flags: {' '.join(args)}")
        cmd, spec, vars_ = parsed
        try:
            if cmd.kind == "download":
                return await self._download(argv, spec, vars_)
            if cmd.kind == "upload":
                return await self._upload(argv, spec, vars_)
            if spec.path.startswith("/graph/"):
                return CliResult(tuple(argv), 0, json.dumps(self._graph(spec)))
            resp = await self._http.send(spec)
        except Exception as exc:                                              # the REST layer's typed errors become a CLI failure on stderr
            words = re.sub(r"(?<!^)(?=[A-Z])", " ", type(exc).__name__.removesuffix("Error")).lower()
            return CliResult(tuple(argv), 1, "", f"{words}: {getattr(exc, 'message', exc)}")
        data = resp.json() if resp.content else None
        if cmd.kind == "csv":
            return CliResult(tuple(argv), 0, self._csv(data))
        if cmd.kind == "count":
            return CliResult(tuple(argv), 0, f"Number of work items in the search: {(data or {}).get('count', 0)}\n")
        if cmd.op in self.text_output:                                         # simulate a version that prints a table instead of JSON
            return CliResult(tuple(argv), 0, "KEY   SUMMARY\nPROJ-1 Login fails\n")
        out = unshape(cmd.wrap, data)
        if self.cmap.envelope:                                                 # e.g. twg: {"apiVersion": "v2", "command": ..., "data": ...}
            out = {"apiVersion": "v2", "command": ".".join(cmd.prefix), "request": {}, self.cmap.envelope: out, "meta": {}}
        return CliResult(tuple(argv), 0, json.dumps(out) if data is not None else "")

    def _inline_files(self, args: tuple[str, ...]) -> tuple[str, ...]:
        """``--body-file <path>``: read the file the way the real CLI does, so the inverse map sees the text itself."""
        flags = {tok[0] for c in self.cmap.all for tok in c.argv if isinstance(tok, tuple) and tok[1].strip("{}") in c.files}
        for c in self.cmap.all:
            for flag, nxt in zip(c.argv, c.argv[1:], strict=False):
                if isinstance(flag, str) and isinstance(nxt, str) and nxt.strip("{}") in c.files:
                    flags.add(flag)
        out = list(args)
        for i in range(len(out) - 1):
            if out[i] in flags and Path(out[i + 1]).is_file():
                out[i + 1] = Path(out[i + 1]).read_text(encoding="utf-8")
        return tuple(out)

    async def _download(self, argv: Sequence[str], spec: RequestSpec, vars_: dict[str, str]) -> CliResult:
        meta = (await self._http.send(spec)).json()
        from dataclasses import replace
        content = (await self._http.send(replace(spec, path=f"/attachment/content/{vars_['attachment_id']}"))).content
        out = Path(vars_["outdir"])
        out.mkdir(parents=True, exist_ok=True)
        (out / meta["filename"]).write_bytes(content)
        return CliResult(tuple(argv), 0, json.dumps({"saved": meta["filename"]}))

    async def _upload(self, argv: Sequence[str], spec: RequestSpec, vars_: dict[str, str]) -> CliResult:
        from dataclasses import replace
        path = Path(vars_["file"])
        resp = await self._http.send(replace(spec, params={}, json=None, files={"file": (path.name, path.read_bytes())}, headers={"X-Atlassian-Token": "no-check"}))
        return CliResult(tuple(argv), 0, resp.text)

    @staticmethod
    def _graph(spec: RequestSpec) -> Any:
        body = spec.json or {}
        if spec.path.endswith("/context"):
            return {"related": [{"ari": "ari:cloud:jira::issue/10001", "type": "issue", "title": "Login fails", "url": "https://example.atlassian.net/browse/PROJ-1"}][: int(body.get("maxItems", 50))]}
        if spec.path.endswith("/objects"):
            return {"objects": [{"ari": a, "title": a} for a in body.get("ariOrUrls", [])]}
        return {"results": [{"title": "Runbook", "url": "https://example.atlassian.net/wiki/spaces/ENG/pages/123", "product": "confluence", "snippet": body.get("query"), "score": 0.9}][: int(body.get("limit", 20))]}

    @staticmethod
    def _csv(data: Any) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["key", "summary", "status"])
        for i in (data or {}).get("issues", []):
            f = i.get("fields") or {}
            w.writerow([i.get("key"), f.get("summary"), (f.get("status") or {}).get("name")])
        return buf.getvalue()

    # ---------------------------------------------------------------------------------------------------- wiring
    def runner(self, binary: str = "fakecli", *, name: str | None = None) -> CliRunner:
        return CliRunner(binary, name=name, executor=self.executor, which=lambda b: f"/fake/bin/{b}")
