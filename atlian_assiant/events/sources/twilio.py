"""TwilioMessageLogSource (DM-23): poll the Twilio Messages API for SMS sent from the configured number (outbound; doubles as a delivery audit)."""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar

import anyio
import httpx

from ..model import ChangeEvent, EventSourceKind
from ..normalize import AtlassianLinkParser
from ..stores.base import CheckpointStore


class TwilioMessageLogSource:
    kind: ClassVar[EventSourceKind] = EventSourceKind.TWILIO_LOG
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-23"})
    requires_public_ingress: ClassVar[bool] = False

    def __init__(self, http: httpx.AsyncClient, checkpoints: CheckpointStore, *, account_sid: str, auth_token: str, from_number: str, project_keys: Iterable[str] = (),
                 interval: float = 60.0, sleep: Callable[[float], Awaitable[None]] = anyio.sleep) -> None:
        self.http, self.checkpoints, self.sid, self._auth, self.from_number = http, checkpoints, account_sid, (account_sid, auth_token), from_number
        self.parser, self.interval, self._sleep, self.stream, self._closed = AtlassianLinkParser(project_keys), interval, sleep, f"twilio:{from_number}", False
        self.delivery_failures: list[dict[str, Any]] = []

    async def poll_once(self) -> list[ChangeEvent]:
        since = await self.checkpoints.load(self.stream)
        params = {"From": self.from_number, "PageSize": "100", **({"DateSent>": since} if since else {})}
        resp = await self.http.get(f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json", params=params, auth=self._auth)
        resp.raise_for_status()
        out: list[ChangeEvent] = []
        newest = since
        for m in resp.json().get("messages", []):
            if m.get("status") in ("failed", "undelivered"):
                self.delivery_failures.append({"sid": m["sid"], "status": m["status"], "error_code": m.get("error_code")})      # delivery audit
            out += self.parser.events(str(m.get("body") or ""), source=self.kind, native_type="sms:twilio", message_id=str(m["sid"]), method_id="DM-23",
                                      received_at=datetime.now(UTC))
            sent = m.get("date_sent")
            if sent:
                iso = parsedate_to_datetime(sent).isoformat()
                newest = iso if newest is None or iso > newest else newest
        if newest and newest != since:
            await self.checkpoints.save(self.stream, newest)
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
