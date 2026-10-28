"""SsmDocumentAdapter (DM-18): the Python side of an SSM Command document that Jira Automation runs on a managed node."""
from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, ClassVar, TextIO

from ..automation import AutomationAction, PayloadMapping
from ..enrich import CanonicalEnricher
from ..model import ChangeEvent, EventSourceKind

Handler = Callable[[ChangeEvent], Awaitable[None]]


def load_handler(path: str) -> Handler:
    """``pkg.mod:fn`` -> callable (the handler registered in the document / JEC action mapping)."""
    module, _, attr = path.partition(":")
    if not module or not attr:
        raise ValueError(f"handler must look like 'package.module:function', got {path!r}")
    fn = getattr(importlib.import_module(module), attr)
    return fn  # type: ignore[no-any-return]


class SsmDocumentAdapter:
    """Parses the ``payload`` parameter, normalizes it with the template's ``PayloadMapping``, optionally enriches through REST and calls the handler.
    ``run`` returns the process exit code (0 = success), which becomes the SSM command status."""

    kind: ClassVar[EventSourceKind] = EventSourceKind.AWS_SSM
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-18"})
    requires_public_ingress: ClassVar[bool] = False

    def __init__(self, handler: Handler, *, mapping: PayloadMapping | None = None, enricher: CanonicalEnricher | None = None, stderr: TextIO | None = None) -> None:
        self.handler, self.enricher, self._err = handler, enricher, stderr or sys.stderr
        self.mapping = mapping or PayloadMapping("jira", "issue_created", AutomationAction.SSM)

    def parse(self, payload: str) -> ChangeEvent:
        data = json.loads(payload)
        if not isinstance(data, Mapping):
            raise ValueError("payload must be a JSON object")
        return self.mapping.normalize(data, method_id="DM-18").model_copy(update={"source": self.kind})

    async def run(self, payload: str) -> int:
        try:
            event = self.parse(payload)
            if self.enricher is not None:
                enriched = await self.enricher.enrich(event)
                if enriched is None:
                    return 0                                    # stale / duplicate version: nothing to do, not a failure
                event = enriched
            await self.handler(event)
        except Exception as exc:                                # exit code is the contract with SSM: non-zero marks the command failed
            print(f"atlian-assiant ssm handler failed: {type(exc).__name__}: {exc}", file=self._err)
            return 1
        return 0

    # EventSource-shaped no-ops: this adapter is pushed to by SSM, it never polls
    async def events(self) -> Any:
        return
        yield

    async def ack(self, event: ChangeEvent) -> None:
        return None

    async def nack(self, event: ChangeEvent, *, error: BaseException) -> None:
        return None

    async def aclose(self) -> None:
        return None
