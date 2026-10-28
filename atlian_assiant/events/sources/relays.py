"""Pull consumers for the managed relays: Azure Service Bus (DM-40), GCP Pub/Sub (DM-41), Cloudflare Queues (DM-42), generic broker (DM-43).

Cloud SDK objects are injected (duck-typed); the ``from_*`` constructors import the SDKs lazily. Messages are relay-normalized events or verified-only relay envelopes
(see ``sqs.message_to_event``); the dedup key is the envelope / webhook identifier.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, ClassVar

import anyio
import httpx

from ..model import ChangeEvent, EventSourceKind
from .sqs import message_to_event


class _PullSource:
    kind: ClassVar[EventSourceKind]
    method_ids: ClassVar[frozenset[str]]
    requires_public_ingress: ClassVar[bool] = False
    method_id: str

    def __init__(self, *, idle_sleep: float = 1.0) -> None:
        self._idle, self._closed = idle_sleep, False
        self._pending: dict[str, Any] = {}               # transport id -> backend handle (lock token / ack id / lease id / broker id)

    async def poll_once(self) -> list[ChangeEvent]:  # pragma: no cover - abstract
        raise NotImplementedError

    async def events(self) -> AsyncIterator[ChangeEvent]:
        while not self._closed:
            batch = await self.poll_once()
            for ev in batch:
                yield ev
            if not batch and not self._closed:
                await anyio.sleep(self._idle)

    async def aclose(self) -> None:
        self._closed = True

    def _track(self, body: str, transport_id: str, handle: Any, attempts: int = 1) -> ChangeEvent:
        ev = message_to_event(body, transport_id, method_id=self.method_id, source=self.kind, receive_count=attempts)
        self._pending[transport_id] = handle
        return ev


class AzureServiceBusSource(_PullSource):
    """DM-40: peek-lock receive. ``complete`` on ack, ``abandon`` on nack (the broker redelivers, then dead-letters after maxDeliveryCount)."""

    kind: ClassVar[EventSourceKind] = EventSourceKind.AZURE_SERVICE_BUS
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-40"})
    method_id = "DM-40"

    def __init__(self, receiver: Any, *, max_messages: int = 10, max_wait: float = 20.0, **kw: Any) -> None:
        super().__init__(**kw)
        self.receiver, self.max_messages, self.max_wait = receiver, max_messages, max_wait

    @classmethod
    def from_connection_string(cls, conn: str, queue: str, **kw: Any) -> AzureServiceBusSource:
        try:
            from azure.servicebus import ServiceBusClient
        except ImportError as exc:
            from ...core.errors import ConfigurationError
            raise ConfigurationError("azure-servicebus is not installed", hint="pip install 'atlian-assiant[azure]'") from exc
        return cls(ServiceBusClient.from_connection_string(conn).get_queue_receiver(queue_name=queue), **kw)

    async def poll_once(self) -> list[ChangeEvent]:
        msgs = await anyio.to_thread.run_sync(lambda: self.receiver.receive_messages(max_message_count=self.max_messages, max_wait_time=self.max_wait))
        return [self._track(b"".join(m.body).decode() if not isinstance(m.body, (str, bytes)) else (m.body if isinstance(m.body, str) else m.body.decode()),
                            str(m.message_id), m, int(getattr(m, "delivery_count", 0) or 1)) for m in msgs]

    async def ack(self, event: ChangeEvent) -> None:
        msg = self._pending.pop(str(event.delivery.transport_id), None)
        if msg is not None:
            await anyio.to_thread.run_sync(self.receiver.complete_message, msg)

    async def nack(self, event: ChangeEvent, *, error: BaseException) -> None:
        msg = self._pending.pop(str(event.delivery.transport_id), None)
        if msg is not None:
            await anyio.to_thread.run_sync(self.receiver.abandon_message, msg)


class PubSubSource(_PullSource):
    """DM-41: Pub/Sub pull. ack acknowledges; nack sets the ack deadline to 0 so the message is redelivered immediately."""

    kind: ClassVar[EventSourceKind] = EventSourceKind.GCP_PUBSUB
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-41"})
    method_id = "DM-41"

    def __init__(self, subscriber: Any, subscription: str, *, max_messages: int = 10, **kw: Any) -> None:
        super().__init__(**kw)
        self.subscriber, self.subscription, self.max_messages = subscriber, subscription, max_messages

    @classmethod
    def from_subscription(cls, subscription: str, **kw: Any) -> PubSubSource:
        try:
            from google.cloud import pubsub_v1
        except ImportError as exc:
            from ...core.errors import ConfigurationError
            raise ConfigurationError("google-cloud-pubsub is not installed", hint="pip install 'atlian-assiant[gcp]'") from exc
        return cls(pubsub_v1.SubscriberClient(), subscription, **kw)

    async def poll_once(self) -> list[ChangeEvent]:
        resp = await anyio.to_thread.run_sync(lambda: self.subscriber.pull(subscription=self.subscription, max_messages=self.max_messages))
        out = []
        for rm in resp.received_messages:
            data = rm.message.data.decode() if isinstance(rm.message.data, bytes) else str(rm.message.data)
            out.append(self._track(data, str(rm.message.message_id), rm.ack_id, int(getattr(rm, "delivery_attempt", 0) or 1)))
        return out

    async def ack(self, event: ChangeEvent) -> None:
        ack_id = self._pending.pop(str(event.delivery.transport_id), None)
        if ack_id is not None:
            await anyio.to_thread.run_sync(lambda: self.subscriber.acknowledge(subscription=self.subscription, ack_ids=[ack_id]))

    async def nack(self, event: ChangeEvent, *, error: BaseException) -> None:
        ack_id = self._pending.pop(str(event.delivery.transport_id), None)
        if ack_id is not None:
            await anyio.to_thread.run_sync(lambda: self.subscriber.modify_ack_deadline(subscription=self.subscription, ack_ids=[ack_id], ack_deadline_seconds=0))


class CloudflareQueuePullSource(_PullSource):
    """DM-42: Cloudflare Queues HTTP pull consumer + ack. The request/response shapes are unverified (VER-25) and isolated in ``_pull`` / ``_ack_call``."""

    kind: ClassVar[EventSourceKind] = EventSourceKind.CLOUDFLARE_QUEUE
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-42"})
    method_id = "DM-42"

    def __init__(self, http: httpx.AsyncClient, *, account_id: str, queue_id: str, api_token: str, batch_size: int = 10, visibility_ms: int = 60000, **kw: Any) -> None:
        super().__init__(**kw)
        self.http, self.batch, self.visibility = http, batch_size, visibility_ms
        self._base = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/queues/{queue_id}/messages"
        self._headers = {"Authorization": f"Bearer {api_token}"}

    async def _pull(self) -> list[dict[str, Any]]:
        resp = await self.http.post(f"{self._base}/pull", headers=self._headers, json={"batch_size": self.batch, "visibility_timeout_ms": self.visibility})
        resp.raise_for_status()
        return list((resp.json().get("result") or {}).get("messages", []))

    async def _ack_call(self, acks: list[str], retries: list[str]) -> None:
        resp = await self.http.post(f"{self._base}/ack", headers=self._headers, json={"acks": [{"lease_id": a} for a in acks], "retries": [{"lease_id": r} for r in retries]})
        resp.raise_for_status()

    async def poll_once(self) -> list[ChangeEvent]:
        import json
        out = []
        for m in await self._pull():
            body = m["body"] if isinstance(m["body"], str) else json.dumps(m["body"])
            out.append(self._track(body, str(m["id"]), m["lease_id"], int(m.get("attempts", 1))))
        return out

    async def ack(self, event: ChangeEvent) -> None:
        lease = self._pending.pop(str(event.delivery.transport_id), None)
        if lease is not None:
            await self._ack_call([lease], [])

    async def nack(self, event: ChangeEvent, *, error: BaseException) -> None:
        lease = self._pending.pop(str(event.delivery.transport_id), None)
        if lease is not None:
            await self._ack_call([], [lease])


class HttpPullBrokerSource(_PullSource):
    """DM-43: a webhook relay / broker service with configurable pull and ack endpoints: ``GET pull_url -> {"messages": [{"id", "body"}]}``,
    ``POST ack_url {"ids": [...]}`` (``{"nack": [...]}`` for failures)."""

    kind: ClassVar[EventSourceKind] = EventSourceKind.HTTP_PULL_BROKER
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-43"})
    method_id = "DM-43"

    def __init__(self, http: httpx.AsyncClient, pull_url: str, ack_url: str, *, auth: Callable[[], Awaitable[dict[str, str]]] | dict[str, str] | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.http, self.pull_url, self.ack_url, self._auth = http, pull_url, ack_url, auth

    async def _headers(self) -> dict[str, str]:
        if callable(self._auth):
            return await self._auth()
        return dict(self._auth or {})

    async def poll_once(self) -> list[ChangeEvent]:
        import json
        resp = await self.http.get(self.pull_url, headers=await self._headers())
        resp.raise_for_status()
        out = []
        for m in resp.json().get("messages", []):
            body = m["body"] if isinstance(m["body"], str) else json.dumps(m["body"])
            out.append(self._track(body, str(m["id"]), str(m["id"])))
        return out

    async def ack(self, event: ChangeEvent) -> None:
        handle = self._pending.pop(str(event.delivery.transport_id), None)
        if handle is not None:
            (await self.http.post(self.ack_url, headers=await self._headers(), json={"ids": [handle]})).raise_for_status()

    async def nack(self, event: ChangeEvent, *, error: BaseException) -> None:
        handle = self._pending.pop(str(event.delivery.transport_id), None)
        if handle is not None:
            (await self.http.post(self.ack_url, headers=await self._headers(), json={"nack": [handle]})).raise_for_status()
