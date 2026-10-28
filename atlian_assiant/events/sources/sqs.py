"""SqsSource (DM-17 / DM-39): long-poll an SQS queue; extend visibility while handlers run; delete on ack; leave for redelivery on nack."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar

import anyio

from ...capabilities import Product
from ..model import ChangeAction, ChangeEvent, DeliveryInfo, EntityType, EventSourceKind
from ..normalize import normalize_automation_envelope, normalize_jira_webhook


def unwrap_sns(body: str) -> str:
    """If raw message delivery is OFF the body is an SNS envelope ``{"Type": "Notification", "Message": "..."}``: return the inner message."""
    try:
        parsed = json.loads(body)
    except ValueError:
        return body
    if isinstance(parsed, dict) and parsed.get("Type") == "Notification" and "Message" in parsed:
        return str(parsed["Message"])
    return body


def message_to_event(body: str, message_id: str, *, method_id: str, source: EventSourceKind, received_at: datetime | None = None,
                     receive_count: int = 0) -> ChangeEvent:
    """SQS body -> ChangeEvent. Four shapes: a relay-normalized ChangeEvent (DM-39), a Jira Automation envelope (DM-17: dedup on ``atl_event_id``),
    a verified-only relay envelope (Cloudflare), or anything else (kept as an UNKNOWN event keyed by the SQS ``MessageId``, never dropped)."""
    text = unwrap_sns(body)
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    when = received_at or datetime.now(UTC)
    if isinstance(payload, Mapping) and "event_id" in payload and "native_type" in payload:
        ev = ChangeEvent.model_validate(payload)                                          # produced by the relay's verifier chain: id is the webhook identifier
        ev = ev.model_copy(update={"delivery": ev.delivery.model_copy(update={"method_id": method_id})})
    elif isinstance(payload, Mapping) and payload.get("relay") and isinstance(payload.get("body"), Mapping):
        # edge relays that only verify (Cloudflare Worker): {"relay": "...", "headers": {...}, "body": <raw webhook JSON>}; normalized here with the original headers
        raw_body = json.dumps(payload["body"]).encode()
        ev = normalize_jira_webhook(payload["body"], payload.get("headers") or {}, method_id=method_id, body=raw_body, received_at=when, verified=True)
        ev = ev.model_copy(update={"source": EventSourceKind.JIRA_WEBHOOK})
    elif isinstance(payload, Mapping) and ("atl_event_id" in payload or "trigger" in payload):
        ev = normalize_automation_envelope(payload, {}, method_id=method_id, body=text.encode(), received_at=when, verified=True)
        ev = ev.model_copy(update={"source": source})
    else:
        ev = ChangeEvent(event_id=message_id, source=source, product=Product.PLATFORM, entity_type=EntityType.UNKNOWN, action=ChangeAction.OTHER,
                         native_type="sqs:unparsed", received_at=when, delivery=DeliveryInfo(method_id=method_id, verified=False), payload={"body": text[:2000]})
    return ev.model_copy(update={"delivery": ev.delivery.model_copy(update={"transport_id": message_id, "attempt": max(ev.delivery.attempt, receive_count - 1)})})


class SqsSource:
    """``sqs`` is any object with boto3's ``receive_message`` / ``delete_message`` / ``change_message_visibility`` (a boto3 client, or a fake).

    * long poll 20 s; at most 10 messages per receive
    * ``heartbeat(stop)`` extends the visibility timeout of in-flight messages so a slow handler does not cause redelivery
    * ``ack`` deletes; ``nack`` leaves the message: it reappears after the visibility timeout and moves to the DLQ after the redrive limit
    """

    kind: ClassVar[EventSourceKind] = EventSourceKind.AWS_SQS
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-17", "DM-39"})
    requires_public_ingress: ClassVar[bool] = False

    def __init__(self, sqs: Any, queue_url: str, *, wait_seconds: int = 20, max_messages: int = 10, visibility_timeout: int = 60, method_id: str = "DM-17",
                 idle_sleep: float = 1.0) -> None:
        self.sqs, self.queue_url, self.wait, self.max, self.visibility, self.method_id, self._idle = sqs, queue_url, wait_seconds, max_messages, visibility_timeout, method_id, idle_sleep
        self._inflight: dict[str, str] = {}          # SQS MessageId -> receipt handle (NOT the dedup key: duplicates share it)
        self._closed = False

    async def poll_once(self) -> list[ChangeEvent]:
        resp = await anyio.to_thread.run_sync(lambda: self.sqs.receive_message(
            QueueUrl=self.queue_url, MaxNumberOfMessages=self.max, WaitTimeSeconds=self.wait, VisibilityTimeout=self.visibility,
            AttributeNames=["ApproximateReceiveCount"]))
        out = []
        for m in resp.get("Messages", []):
            count = int(m.get("Attributes", {}).get("ApproximateReceiveCount", 1))
            ev = message_to_event(m["Body"], m["MessageId"], method_id=self.method_id, source=self.kind, receive_count=count)
            self._inflight[m["MessageId"]] = m["ReceiptHandle"]
            out.append(ev)
        return out

    async def events(self) -> AsyncIterator[ChangeEvent]:
        while not self._closed:
            batch = await self.poll_once()
            for ev in batch:
                yield ev
            if not batch and not self._closed:
                await anyio.sleep(self._idle)

    async def heartbeat(self, stop: anyio.Event, *, every: float | None = None) -> None:
        """Run alongside the pipeline: re-extends the visibility of every message still in flight until ``stop`` is set."""
        interval = every if every is not None else self.visibility / 2
        while not stop.is_set():
            with anyio.move_on_after(interval):
                await stop.wait()
            for handle in list(self._inflight.values()):
                await anyio.to_thread.run_sync(self._extend, handle)

    def _extend(self, handle: str) -> None:
        self.sqs.change_message_visibility(QueueUrl=self.queue_url, ReceiptHandle=handle, VisibilityTimeout=self.visibility)

    async def ack(self, event: ChangeEvent) -> None:
        handle = self._inflight.pop(str(event.delivery.transport_id), None)
        if handle:
            await anyio.to_thread.run_sync(lambda: self.sqs.delete_message(QueueUrl=self.queue_url, ReceiptHandle=handle))

    async def nack(self, event: ChangeEvent, *, error: BaseException) -> None:
        self._inflight.pop(str(event.delivery.transport_id), None)    # not deleted: redelivered after the visibility timeout, then DLQ after maxReceiveCount

    async def aclose(self) -> None:
        self._closed = True
