"""WebhookFailedReplaySource (DM-05): turn Jira's failed-webhook list back into events."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, ClassVar

import anyio

from ...capabilities import Product
from ...models.webhooks import FailedWebhook
from ..model import ChangeAction, ChangeEvent, DeliveryInfo, EntityType, EventSourceKind
from ..normalize import normalize_jira_webhook
from ..stores.base import CheckpointStore


class WebhookFailedReplaySource:
    """Polls ``GET /rest/api/3/webhook/failed`` and re-emits each failed delivery's body as an event.

    The dedup key is the failure id; if Atlassian's own retry also delivers the event, the pipeline's version gate (fresh read) makes it harmless.
    A failure with no body (``excludeBody``) becomes an UNKNOWN event so the entity can be found and re-fetched.
    """

    kind: ClassVar[EventSourceKind] = EventSourceKind.WEBHOOK_FAILED_REPLAY
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-05"})
    requires_public_ingress: ClassVar[bool] = False

    def __init__(self, fetch_failed: Callable[[datetime | None], Awaitable[list[FailedWebhook]]], checkpoints: CheckpointStore, *,
                 interval: float = 300.0, stream: str = "webhook-failed", clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 sleep: Callable[[float], Awaitable[None]] = anyio.sleep) -> None:
        self._fetch, self.checkpoints, self.interval, self.stream = fetch_failed, checkpoints, interval, stream
        self._clock, self._sleep, self._closed = clock, sleep, False

    @classmethod
    def from_client(cls, client: Any, checkpoints: CheckpointStore, **kwargs: Any) -> WebhookFailedReplaySource:
        return cls(lambda after: client.webhooks.failed(after=after), checkpoints, **kwargs)

    def _event(self, failure: FailedWebhook) -> ChangeEvent:
        body = (failure.body or "").encode()
        try:
            payload = json.loads(failure.body or "")
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            event = normalize_jira_webhook(payload, {}, method_id="DM-05", body=body, received_at=self._clock(), source=self.kind)
            return event.model_copy(update={"event_id": f"failed:{failure.id}", "delivery": event.delivery.model_copy(
                update={"transport_id": failure.id, "verified": True})})
        return ChangeEvent(event_id=f"failed:{failure.id}", source=self.kind, product=Product.JIRA, entity_type=EntityType.UNKNOWN, action=ChangeAction.OTHER,
                           native_type="webhook:failed", occurred_at=failure.failure_time, received_at=self._clock(),
                           delivery=DeliveryInfo(method_id="DM-05", transport_id=failure.id, verified=True), payload={"url": failure.url})

    async def poll_once(self) -> list[ChangeEvent]:
        raw = await self.checkpoints.load(self.stream)
        after = datetime.fromisoformat(raw) if raw else None
        failures = await self._fetch(after)
        events = [self._event(f) for f in failures]
        times = [f.failure_time for f in failures if f.failure_time]
        if times:
            await self.checkpoints.save(self.stream, max(times).isoformat())
        return events

    async def events(self) -> AsyncIterator[ChangeEvent]:
        while not self._closed:
            for event in await self.poll_once():
                yield event
            if self._closed:
                break
            await self._sleep(self.interval)

    async def ack(self, event: ChangeEvent) -> None:
        return None

    async def nack(self, event: ChangeEvent, *, error: BaseException) -> None:
        return None

    async def aclose(self) -> None:
        self._closed = True
