"""TeamsChannelPollingSource (DM-22, DM-34): Microsoft Graph channel-message DELTA polling (outbound). Graph change notifications are avoided: they need a public URL."""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import Any, ClassVar

import anyio
import httpx

from ..human.parsers import teams_text
from ..model import ChangeEvent, EventSourceKind
from ..normalize import AtlassianLinkParser
from ..stores.base import CheckpointStore

GRAPH = "https://graph.microsoft.com/v1.0"


class TeamsChannelPollingSource:
    """``/teams/{team}/channels/{channel}/messages/delta``: the ``@odata.deltaLink`` is checkpointed so each poll returns only new/changed messages.
    Needs a channel-message read permission (which one is VER-21): the token provider supplies the bearer token."""

    kind: ClassVar[EventSourceKind] = EventSourceKind.TEAMS_GRAPH
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-22", "DM-34"})
    requires_public_ingress: ClassVar[bool] = False

    def __init__(self, http: httpx.AsyncClient, token: Callable[[], Awaitable[str]], checkpoints: CheckpointStore, *, team_id: str, channel_id: str,
                 project_keys: Iterable[str] = (), method_id: str = "DM-34", interval: float = 60.0, sleep: Callable[[float], Awaitable[None]] = anyio.sleep) -> None:
        self.http, self._token, self.checkpoints = http, token, checkpoints
        self.team_id, self.channel_id, self.method_id, self.interval, self._sleep = team_id, channel_id, method_id, interval, sleep
        self.parser, self.stream, self._closed = AtlassianLinkParser(project_keys), f"teams:{team_id}:{channel_id}", False

    async def poll_once(self) -> list[ChangeEvent]:
        url: str | None = await self.checkpoints.load(self.stream) or f"{GRAPH}/teams/{self.team_id}/channels/{self.channel_id}/messages/delta"
        headers = {"Authorization": f"Bearer {await self._token()}"}
        out: list[ChangeEvent] = []
        while url:
            resp = await self.http.get(url, headers=headers)
            resp.raise_for_status()
            body: dict[str, Any] = resp.json()
            for m in body.get("value", []):
                if m.get("deletedDateTime"):
                    continue
                out += self.parser.events(teams_text(m), source=self.kind, native_type="teams:message", message_id=str(m["id"]), method_id=self.method_id,
                                          received_at=datetime.now(UTC))
            if body.get("@odata.deltaLink"):
                await self.checkpoints.save(self.stream, body["@odata.deltaLink"])
                break
            url = body.get("@odata.nextLink")
        return out

    async def events(self) -> AsyncIterator[ChangeEvent]:
        while not self._closed:
            for ev in await self.poll_once():
                yield ev
            if self._closed:
                break
            await self._sleep(self.interval)

    async def ack(self, event: ChangeEvent) -> None:
        return None

    async def nack(self, event: ChangeEvent, *, error: BaseException) -> None:
        return None

    async def aclose(self) -> None:
        self._closed = True
