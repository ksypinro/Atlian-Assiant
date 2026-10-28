"""Framework adapters: raw ASGI (no dependency), FastAPI/Starlette router and Flask blueprint."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, MutableMapping, Sequence
from typing import Any

from .base import InboundRequest, InboundResponse, WebhookEndpoint

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]


def _match(endpoints: Sequence[WebhookEndpoint], path: str) -> WebhookEndpoint | None:
    return next((e for e in endpoints if e.path == path), None)


def as_asgi_app(endpoints: Sequence[WebhookEndpoint], *, max_body_bytes: int = 5 * 1024 * 1024) -> Callable[[Scope, Receive, Send], Awaitable[None]]:
    """A plain ASGI app routing by exact path. Streams the body with a hard size cap (413) so a flood cannot exhaust memory."""
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        body, too_big = b"", False
        while True:
            message = await receive()
            body += message.get("body", b"")
            if len(body) > max_body_bytes:
                too_big = True
                break
            if not message.get("more_body"):
                break
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        client = scope.get("client")
        endpoint = _match(endpoints, scope["path"])
        if too_big:
            resp = InboundResponse(413)
        elif endpoint is None:
            resp = InboundResponse(404)
        else:
            query = {k: v for k, v in (p.split("=", 1) if "=" in p else (p, "") for p in scope.get("query_string", b"").decode().split("&") if p)}
            resp = await endpoint.handle(InboundRequest(scope["method"], scope["path"], headers, body, query, client[0] if client else None))
        await send({"type": "http.response.start", "status": resp.status, "headers": [(b"content-length", str(len(resp.body)).encode())]})
        await send({"type": "http.response.body", "body": resp.body})
    return app


def fastapi_router(endpoints: Sequence[WebhookEndpoint]) -> Any:
    """A FastAPI ``APIRouter`` (needs the ``server`` extra). One POST route per endpoint path."""
    try:
        from fastapi import APIRouter, Request, Response
    except ImportError as exc:
        raise ImportError("fastapi is required for fastapi_router(); install it or use as_asgi_app()") from exc
    router = APIRouter()
    for endpoint in endpoints:
        async def route(request: Request, _endpoint: WebhookEndpoint = endpoint) -> Response:
            resp = await _endpoint.handle(InboundRequest(request.method, request.url.path, dict(request.headers), await request.body(),
                                                         dict(request.query_params), request.client.host if request.client else None))
            return Response(status_code=resp.status, content=resp.body)
        router.add_api_route(endpoint.path, route, methods=["POST"])
    return router


def flask_blueprint(endpoints: Sequence[WebhookEndpoint], name: str = "atlassian_events") -> Any:
    """A Flask ``Blueprint`` (sync Flask: each request runs the async handler to completion)."""
    try:
        import anyio
        from flask import Blueprint, Response, request
    except ImportError as exc:
        raise ImportError("flask is required for flask_blueprint(); install it or use as_asgi_app()") from exc
    bp = Blueprint(name, __name__)
    for i, endpoint in enumerate(endpoints):
        def view(_endpoint: WebhookEndpoint = endpoint) -> Any:
            req = InboundRequest(request.method, request.path, dict(request.headers), request.get_data(), dict(request.args), request.remote_addr)
            resp = anyio.run(_endpoint.handle, req)
            return Response(resp.body, status=resp.status)
        bp.add_url_rule(endpoint.path, endpoint=f"hook_{i}", view_func=view, methods=["POST"])
    return bp


def headers_dict(raw: Mapping[str, str]) -> dict[str, str]:
    return {k.lower(): v for k, v in raw.items()}
