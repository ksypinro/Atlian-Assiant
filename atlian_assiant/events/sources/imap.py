"""ImapSource (DM-20, DM-25..29): Atlassian notification emails -> events. Outbound IMAP only; read-only folder; sender allow-list; digest splitting."""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import Any, ClassVar

import anyio

from ..human.parsers import ParsedEmail, parse_email
from ..model import ChangeEvent, EventSourceKind
from ..normalize import AtlassianLinkParser
from ..stores.base import CheckpointStore


class ImapSource:
    """``imap`` is an ``imaplib.IMAP4_SSL``-shaped object (select / uid). The folder is opened READ-ONLY, so the mailbox is never modified:
    progress is tracked with a UID checkpoint, duplicates by ``Message-ID``. Mail from senders outside ``allowed_senders`` is counted and ignored."""

    kind: ClassVar[EventSourceKind] = EventSourceKind.EMAIL_IMAP
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-20", "DM-25", "DM-26", "DM-27", "DM-28", "DM-29"})
    requires_public_ingress: ClassVar[bool] = False

    def __init__(self, imap: Any, checkpoints: CheckpointStore, *, allowed_senders: Iterable[str], project_keys: Iterable[str] = (), folder: str = "INBOX",
                 method_id: str = "DM-25", require_authenticated: bool = False, interval: float = 60.0, stream: str | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC), sleep: Callable[[float], Awaitable[None]] = anyio.sleep) -> None:
        self.imap, self.checkpoints, self.folder, self.method_id = imap, checkpoints, folder, method_id
        self.allowed = tuple(a.lower() for a in allowed_senders)
        self.parser = AtlassianLinkParser(project_keys)
        self.require_authenticated, self.interval, self._clock, self._sleep = require_authenticated, interval, clock, sleep
        self.stream, self._closed = stream or f"imap:{folder}", False
        self.ignored = {"sender": 0, "unauthenticated": 0}

    def _sender_ok(self, sender: str) -> bool:
        return any(sender == a or (a.startswith("@") and sender.endswith(a)) for a in self.allowed)

    def _authenticated(self, mail: ParsedEmail) -> bool:
        results = mail.headers.get("Authentication-Results", "").lower()
        return "dkim=pass" in results or "spf=pass" in results

    def _fetch(self, last_uid: int) -> list[tuple[int, bytes]]:
        self.imap.select(self.folder, readonly=True)
        typ, data = self.imap.uid("search", None, f"UID {last_uid + 1}:*")
        out: list[tuple[int, bytes]] = []
        for uid in (int(x) for x in (data[0] or b"").split()):
            if uid <= last_uid:
                continue                                     # IMAP returns the last message for `n:*` even when n is past the end
            typ, parts = self.imap.uid("fetch", str(uid), "(RFC822)")
            raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
            if raw:
                out.append((uid, raw))
        return out

    async def poll_once(self) -> list[ChangeEvent]:
        last = int(await self.checkpoints.load(self.stream) or 0)
        fetched = await anyio.to_thread.run_sync(self._fetch, last)
        events: list[ChangeEvent] = []
        for uid, raw in fetched:
            mail = parse_email(raw)
            if not self._sender_ok(mail.sender):
                self.ignored["sender"] += 1
            elif self.require_authenticated and not self._authenticated(mail):
                self.ignored["unauthenticated"] += 1
            else:
                events += self.parser.events(f"{mail.subject}\n{mail.text}", source=self.kind, native_type=f"email:{mail.product_hint}", message_id=mail.message_id,
                                             method_id=self.method_id, received_at=self._clock())
            last = max(last, uid)
        if fetched:
            await self.checkpoints.save(self.stream, str(last))
        return events

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
