"""AuditWebhookReceiver (DM-13) and AuditGapReconciler: audit webhooks retry only 3 times, are unordered and carry no SLO (EV section 20)."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from ...models.admin import AuditEvent, audit_event_from_api
from ...models.common import Page
from ..model import ChangeEvent, EventSourceKind
from ..normalize import audit_change_event
from ..stores.base import DedupStore, LocalQueue
from .base import InboundRequest, QueueEndpoint, RequestVerifier


class AuditWebhookReceiver(QueueEndpoint):
    """Receives organization audit-log webhooks. Dedup key: ``event.id`` (duplicates keep the same id). Authenticate with a ``StaticHeaderVerifier``
    on the optional Authorization header configured in the admin UI, plus an IP allow-list where possible."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-13"})
    requires_public_ingress: ClassVar[bool] = True
    kind: ClassVar[EventSourceKind] = EventSourceKind.AUDIT_WEBHOOK

    def __init__(self, queue: LocalQueue, *, path: str = "/hooks/audit", verifiers: Sequence[RequestVerifier] = (), receipts: DedupStore | None = None,
                 **kwargs: Any) -> None:
        super().__init__(queue, path=path, verifiers=verifiers, receipts=receipts, **kwargs)
        self.seen_ids: set[str] = set()

    def normalize(self, request: InboundRequest, payload: Mapping[str, Any]) -> ChangeEvent:
        event = audit_change_event(audit_event_from_api(payload), source=self.kind, method_id="DM-13", received_at=self._clock(), verified=bool(self.verifiers))
        self.seen_ids.add(event.event_id)       # normalized deliveries (verified); duplicates collapse in the set
        return event


@dataclass(frozen=True)
class GapReport:
    window_start: datetime
    window_end: datetime
    via_webhook: int
    via_api: int
    missing: tuple[str, ...]


class AuditGapReconciler:
    """Compares event ids received by webhook with ``/events`` for the same window; re-emits the missing ones from the API (DM-13 correctness path)."""

    def __init__(self, list_events: Callable[..., Awaitable[Page[AuditEvent]]], received_ids: Callable[[], set[str]], *,
                 grace: timedelta = timedelta(minutes=10), clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._list, self._received, self.grace, self._clock = list_events, received_ids, grace, clock

    @classmethod
    def from_client(cls, client: Any, received_ids: Callable[[], set[str]], **kw: Any) -> AuditGapReconciler:
        return cls(lambda **a: client.admin.audit.events(**a), received_ids, **kw)

    async def reconcile(self, window: timedelta = timedelta(hours=1)) -> tuple[GapReport, list[ChangeEvent]]:
        """Events older than ``grace`` (webhooks may lag) that the API has but the webhook never delivered."""
        end = self._clock()
        start = end - window
        have = self._received()
        items: list[AuditEvent] = []
        cursor: str | None = None
        while True:
            page = await self._list(start=start, end=end, cursor=cursor, limit=500)
            items += page.items
            if not page.next_cursor:
                break
            cursor = page.next_cursor
        settled = [e for e in items if e.occurred_at is None or e.occurred_at <= end - self.grace]
        missing = [e for e in settled if e.id not in have]
        recovered = [audit_change_event(e, source=EventSourceKind.AUDIT_WEBHOOK, method_id="DM-13", received_at=end) for e in missing]
        return GapReport(start, end, len(have), len(items), tuple(e.id for e in missing)), recovered
