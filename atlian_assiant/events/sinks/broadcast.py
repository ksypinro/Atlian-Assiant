"""SSE and WebSocket broadcasters: ASGI routes ``/events/stream`` for internal dashboards and agents."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from ..model import ChangeEvent
from .base import event_json

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]


class _Fanout:
    """Bounded per-subscriber queues: a slow client is dropped, it never blocks the pipeline."""

    def __init__(self, max_buffer: int = 100) -> None:
        self.max_buffer = max_buffer
        self._subs: list[MemoryObjectSendStream[str]] = []
        self.dropped = 0

    def subscribe(self) -> MemoryObjectReceiveStream[str]:
        send, recv = anyio.create_memory_object_stream[str](self.max_buffer)
        self._subs.append(send)
        return recv

    async def broadcast(self, text: str) -> None:
        for send in list(self._subs):
            try:
                send.send_nowait(text)
            except anyio.WouldBlock:
                self._subs.remove(send)
                send.close()
                self.dropped += 1
            except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                self._subs.remove(send)

    @property
    def subscribers(self) -> int:
        return len(self._subs)


def _allowed(scope: Scope, token: str | None) -> bool:
    if token is None:
        return True
    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
    return headers.get("authorization") == f"Bearer {token}"


class SseBroadcaster(_Fanout):
    """``publish`` is the sink; ``asgi_app`` serves ``text/event-stream`` on ``path``. ``token`` (optional) requires ``Authorization: Bearer``."""

    def __init__(self, *, path: str = "/events/stream", token: str | None = None, max_buffer: int = 100) -> None:
        super().__init__(max_buffer)
        self.path, self.token = path, token

    async def publish(self, event: ChangeEvent) -> None:
        await self.broadcast(f"id: {event.dedup_key}\nevent: {event.native_type}\ndata: {event_json(event)}\n\n")

    async def asgi_app(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] != self.path:
            await send({"type": "http.response.start", "status": 404, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return
        if not _allowed(scope, self.token):
            await send({"type": "http.response.start", "status": 401, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/event-stream"), (b"cache-control", b"no-cache")]})
        recv = self.subscribe()
        try:
            async for chunk in recv:
                await send({"type": "http.response.body", "body": chunk.encode(), "more_body": True})
        finally:
            await send({"type": "http.response.body", "body": b""})


class WebSocketBroadcaster(_Fanout):
    def __init__(self, *, path: str = "/events/ws", token: str | None = None, max_buffer: int = 100) -> None:
        super().__init__(max_buffer)
        self.path, self.token = path, token

    async def publish(self, event: ChangeEvent) -> None:
        await self.broadcast(event_json(event))

    async def asgi_app(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "websocket" or scope["path"] != self.path or not _allowed(scope, self.token):
            await receive()
            await send({"type": "websocket.close", "code": 1008})
            return
        await receive()                                           # websocket.connect
        await send({"type": "websocket.accept"})
        recv = self.subscribe()
        async with anyio.create_task_group() as tg:
            async def pump() -> None:
                async for text in recv:
                    await send({"type": "websocket.send", "text": text})

            async def watch() -> None:
                while True:
                    message = await receive()
                    if message["type"] == "websocket.disconnect":
                        tg.cancel_scope.cancel()
                        return
            tg.start_soon(pump)
            tg.start_soon(watch)
