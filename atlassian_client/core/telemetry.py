"""Structured logging and optional OpenTelemetry spans (design section 7.10). Bodies are never recorded by default."""
from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable, Iterator
from typing import Any

from .redaction import RedactingFilter, Redactor

logger = logging.getLogger("atlassian_client")

RequestHook = Callable[[dict[str, Any]], None]


class Telemetry:
    def __init__(self, redactor: Redactor | None = None) -> None:
        self.redactor = redactor or Redactor()
        if not any(isinstance(f, RedactingFilter) for f in logger.filters):
            logger.addFilter(RedactingFilter(self.redactor))
        self.on_request: list[RequestHook] = []
        self.on_response: list[RequestHook] = []

    @contextlib.contextmanager
    def span(self, operation: str, **attrs: Any) -> Iterator[dict[str, Any]]:
        """Span named ``atlassian.<operation>``; uses OpenTelemetry when installed, otherwise a log line."""
        info: dict[str, Any] = {"operation": operation, **attrs}
        start = time.perf_counter()
        otel_cm: Any = None
        try:
            from opentelemetry import trace  # type: ignore[import-not-found,unused-ignore]

            otel_cm = trace.get_tracer("atlassian_client").start_as_current_span(f"atlassian.{operation}")
            span = otel_cm.__enter__()
            for k, v in attrs.items():
                span.set_attribute(k, str(v))
        except ImportError:
            otel_cm = None
        try:
            yield info
        finally:
            info["duration_ms"] = round((time.perf_counter() - start) * 1000, 1)
            if otel_cm is not None:
                otel_cm.__exit__(None, None, None)
            logger.debug("%s", self.redactor.mapping(info))

    def request(self, info: dict[str, Any]) -> None:
        safe = self.redactor.mapping(info)
        for hook in self.on_request:
            hook(safe)

    def response(self, info: dict[str, Any]) -> None:
        safe = self.redactor.mapping(info)
        for hook in self.on_response:
            hook(safe)
