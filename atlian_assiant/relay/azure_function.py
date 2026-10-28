"""Azure relay (DM-40): HTTP-triggered Function -> Service Bus output binding."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .core import build_receiver, process

_receiver: Any = None


def handle(method: str, path: str, headers: Mapping[str, str], body: bytes, send: Callable[[str], None], remote_addr: str | None = None) -> int:
    """Platform-neutral core: returns the HTTP status; calls ``send(message)`` (the Service Bus output) only for verified events."""
    global _receiver
    if _receiver is None:
        _receiver = build_receiver()
    status, message = process(_receiver, method, path, headers, body, remote_addr)
    if message is not None:
        send(message)
    return status


def main(req: Any, msg: Any) -> Any:
    """Azure Functions entry (``azure.functions`` is imported lazily): ``msg`` is the Service Bus output binding."""
    import azure.functions as func
    path = "/" + str(req.route_params.get("path", "hooks/jira")).lstrip("/")
    return func.HttpResponse(status_code=handle(req.method, path, dict(req.headers), req.get_body(), msg.set))
