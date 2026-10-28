"""SlackSocketModeSource (DM-21, DM-32, DM-33): Socket Mode is an OUTBOUND WebSocket, so no public URL is needed."""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime
from typing import Any, ClassVar

from ..human.parsers import slack_text
from ..model import ChangeEvent, EventSourceKind
from ..normalize import AtlassianLinkParser


class SlackSocketModeSource:
    """``socket`` needs ``async receive() -> envelope | None`` and ``async acknowledge(envelope_id)`` (``from_tokens`` builds one with slack_sdk).
    Only messages in ``channels`` (allow-list), never from this app's own bot, are turned into events; the dedup key is ``channel:ts``."""

    kind: ClassVar[EventSourceKind] = EventSourceKind.SLACK_SOCKET_MODE
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-21", "DM-32", "DM-33"})
    requires_public_ingress: ClassVar[bool] = False

    def __init__(self, socket: Any, *, channels: Iterable[str], project_keys: Iterable[str] = (), own_bot_id: str | None = None, method_id: str = "DM-32") -> None:
        self.socket, self.channels, self.parser, self.own_bot, self.method_id = socket, frozenset(channels), AtlassianLinkParser(project_keys), own_bot_id, method_id
        self._closed = False
        self.ignored = {"channel": 0, "self": 0, "type": 0}

    @classmethod
    def from_tokens(cls, app_token: str, **kw: Any) -> SlackSocketModeSource:
        try:
            from slack_sdk.socket_mode.aiohttp import SocketModeClient
        except ImportError as exc:
            from ...core.errors import ConfigurationError
            raise ConfigurationError("slack-sdk is not installed", hint="pip install 'atlian-assiant[slack]'") from exc
        import anyio
        client = SocketModeClient(app_token=app_token)
        send, recv = anyio.create_memory_object_stream[Any](100)

        class _Adapter:
            connected = False

            async def receive(self_inner) -> Any:
                if not self_inner.connected:                  # open the outbound WebSocket on first use (without it nothing is ever delivered)
                    await client.connect()
                    self_inner.connected = True
                return await recv.receive()

            async def acknowledge(self_inner, envelope_id: str) -> None:
                from slack_sdk.socket_mode.response import SocketModeResponse
                await client.send_socket_mode_response(SocketModeResponse(envelope_id=envelope_id))

            async def close(self_inner) -> None:
                if self_inner.connected:
                    await client.disconnect()
                    self_inner.connected = False
                await client.close()

        async def listener(_: Any, request: Any) -> None:
            await send.send({"envelope_id": request.envelope_id, "type": request.type, "payload": request.payload})
        client.socket_mode_request_listeners.append(listener)
        return cls(_Adapter(), **kw)

    async def convert(self, envelope: dict[str, Any]) -> list[ChangeEvent]:
        """Acknowledge the envelope (Slack redelivers otherwise), then normalize it if it is an allowed channel message."""
        await self.socket.acknowledge(envelope["envelope_id"])
        event = (envelope.get("payload") or {}).get("event") or {}
        if envelope.get("type") != "events_api" or event.get("type") != "message" or event.get("subtype") == "message_deleted":
            self.ignored["type"] += 1
            return []
        if event.get("channel") not in self.channels:
            self.ignored["channel"] += 1
            return []
        if self.own_bot and event.get("bot_id") == self.own_bot:
            self.ignored["self"] += 1
            return []
        return self.parser.events(slack_text(event), source=self.kind, native_type="slack:message", message_id=f"{event.get('channel')}:{event.get('ts')}",
                                  method_id=self.method_id, received_at=datetime.now(UTC))

    async def events(self) -> AsyncIterator[ChangeEvent]:
        while not self._closed:
            envelope = await self.socket.receive()
            if envelope is None:
                break
            for ev in await self.convert(envelope):
                yield ev

    async def ack(self, event: ChangeEvent) -> None:
        return None

    async def nack(self, event: ChangeEvent, *, error: BaseException) -> None:
        return None

    async def aclose(self) -> None:
        self._closed = True
        close = getattr(self.socket, "close", None)
        if close is not None:
            await close()
