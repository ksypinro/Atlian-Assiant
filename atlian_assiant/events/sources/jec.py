"""JecScriptAdapter (DM-19): Jira Edge Connector runs this script for an action mapping; exit code 0 means success."""
from __future__ import annotations

import json
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, ClassVar, Protocol, TextIO

import anyio

from ..automation import AutomationAction, PayloadMapping
from ..enrich import CanonicalEnricher
from ..model import ChangeEvent, EventSourceKind


class JecPayloadParser(Protocol):
    """Turns JEC's argument/stdin convention into the payload dict. Pluggable because the exact convention is unverified (VER-02)."""

    def parse(self, argv: Sequence[str], stdin: str) -> Mapping[str, Any]: ...


class DefaultJecPayloadParser:
    """Tolerant default: a JSON object in ``--payload`` / ``-payload``, or on stdin; ``--key value`` pairs become fields; ``-key=value`` too."""

    def parse(self, argv: Sequence[str], stdin: str) -> Mapping[str, Any]:
        out: dict[str, Any] = {}
        args = list(argv)
        i = 0
        while i < len(args):
            a = args[i]
            if a.lstrip("-") in ("payload",) and i + 1 < len(args):
                loaded = json.loads(args[i + 1])
                if isinstance(loaded, Mapping):
                    out.update(loaded)
                i += 2
                continue
            if a.startswith("-") and "=" in a:
                k, _, v = a.lstrip("-").partition("=")
                out[k] = v
            elif a.startswith("-") and i + 1 < len(args) and not args[i + 1].startswith("-"):
                out[a.lstrip("-")] = args[i + 1]
                i += 1
            i += 1
        if stdin.strip():
            try:
                loaded = json.loads(stdin)
            except ValueError:
                loaded = None
            if isinstance(loaded, Mapping):
                out = {**loaded, **out}
        return out


Handler = Callable[[ChangeEvent], Awaitable[None]]


class JecScriptAdapter:
    kind: ClassVar[EventSourceKind] = EventSourceKind.JEC
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-19"})
    requires_public_ingress: ClassVar[bool] = False

    def __init__(self, handler: Handler, *, parser: JecPayloadParser | None = None, mapping: PayloadMapping | None = None,
                 enricher: CanonicalEnricher | None = None, stderr: TextIO | None = None) -> None:
        self.handler, self.parser, self.enricher, self._err = handler, parser or DefaultJecPayloadParser(), enricher, stderr or sys.stderr
        self.mapping = mapping or PayloadMapping("jira", "issue_created", AutomationAction.JEC)

    async def run(self, argv: Sequence[str], stdin: str = "") -> int:
        try:
            payload = self.parser.parse(argv, stdin)
            event = self.mapping.normalize(payload, method_id="DM-19").model_copy(update={"source": self.kind})
            if self.enricher is not None:
                enriched = await self.enricher.enrich(event)
                if enriched is None:
                    return 0
                event = enriched
            await self.handler(event)
        except Exception as exc:
            print(f"atlian-assiant jec handler failed: {type(exc).__name__}: {exc}", file=self._err)
            return 1
        return 0

    async def events(self) -> Any:
        return
        yield

    async def ack(self, event: ChangeEvent) -> None:
        return None

    async def nack(self, event: ChangeEvent, *, error: BaseException) -> None:
        return None

    async def aclose(self) -> None:
        return None


def _read_stdin() -> str:
    """stdin when JEC pipes the payload in; never blocks on a terminal and never fails when stdin is closed."""
    try:
        return "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
    except (OSError, ValueError):
        return ""


def run_jec_handler(handler: Handler, argv: list[str] | None = None, *, stdin: str | None = None, parser: JecPayloadParser | None = None) -> int:
    """Entry point for a script registered in JEC ``actionMappings`` (EV section 25). Returns the process exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    has_payload = any(a.lstrip("-") == "payload" for a in args)
    text = stdin if stdin is not None else ("" if has_payload else _read_stdin())
    return anyio.run(JecScriptAdapter(handler, parser=parser).run, args, text)
