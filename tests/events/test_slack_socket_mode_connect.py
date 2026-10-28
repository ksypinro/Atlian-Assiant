"""SlackSocketModeSource.from_tokens must open the Socket Mode WebSocket (it never did) and close it on aclose()."""
from __future__ import annotations

import sys
import types

import pytest

from atlian_assiant.events.sources.slack import SlackSocketModeSource

pytestmark = pytest.mark.anyio


class FakeSocketModeClient:
    instances: list[FakeSocketModeClient] = []

    def __init__(self, app_token: str) -> None:
        self.app_token, self.socket_mode_request_listeners, self.log = app_token, [], []
        FakeSocketModeClient.instances.append(self)

    async def connect(self) -> None:
        self.log.append("connect")
        req = types.SimpleNamespace(envelope_id="e1", type="events_api",
                                    payload={"event": {"type": "message", "channel": "C1", "ts": "1.0", "text": "KAN-1 updated by Ann"}})
        for listener in self.socket_mode_request_listeners:
            await listener(self, req)

    async def send_socket_mode_response(self, response: object) -> None:
        self.log.append(f"ack:{response.envelope_id}")                         # type: ignore[attr-defined]

    async def disconnect(self) -> None:
        self.log.append("disconnect")

    async def close(self) -> None:
        self.log.append("close")


@pytest.fixture
def fake_slack_sdk(monkeypatch):
    FakeSocketModeClient.instances.clear()
    aio = types.ModuleType("slack_sdk.socket_mode.aiohttp")
    aio.SocketModeClient = FakeSocketModeClient                                 # type: ignore[attr-defined]
    resp = types.ModuleType("slack_sdk.socket_mode.response")
    resp.SocketModeResponse = lambda envelope_id: types.SimpleNamespace(envelope_id=envelope_id)  # type: ignore[attr-defined]
    for name, mod in {"slack_sdk": types.ModuleType("slack_sdk"), "slack_sdk.socket_mode": types.ModuleType("slack_sdk.socket_mode"),
                      "slack_sdk.socket_mode.aiohttp": aio, "slack_sdk.socket_mode.response": resp}.items():
        monkeypatch.setitem(sys.modules, name, mod)


async def test_from_tokens_connects_on_first_read_acknowledges_and_disconnects(fake_slack_sdk):
    src = SlackSocketModeSource.from_tokens("xapp-test", channels=["C1"], project_keys=["KAN"])
    events = src.events()
    first = await events.__anext__()
    client = FakeSocketModeClient.instances[0]
    assert client.log[:2] == ["connect", "ack:e1"] and first.entity_key == "KAN-1"
    await src.aclose()
    assert client.log[-2:] == ["disconnect", "close"]
