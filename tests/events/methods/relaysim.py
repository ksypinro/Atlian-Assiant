"""Fakes for the relay consumers: Service Bus receiver, Pub/Sub subscriber, Cloudflare Queues API, broker."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx

from atlian_assiant.capabilities import Product
from atlian_assiant.events.model import ChangeAction, ChangeEvent, DeliveryInfo, EntityType, EventSourceKind


def relay_message(identifier: str = "wh-1", key: str = "PROJ-1") -> str:
    """What a relay enqueues: a minimal, already-normalized event."""
    ev = ChangeEvent(event_id=identifier, source=EventSourceKind.JIRA_WEBHOOK, product=Product.JIRA, entity_type=EntityType.ISSUE, action=ChangeAction.UPDATED,
                     native_type="jira:issue_updated", entity_key=key, received_at=datetime(2026, 9, 1, tzinfo=UTC), delivery=DeliveryInfo(method_id="DM-39", verified=True))
    return ev.model_dump_json()


def envelope_message(identifier: str = "wh-9", key: str = "PROJ-9") -> str:
    """What the verify-only Cloudflare Worker enqueues."""
    return json.dumps({"relay": "cloudflare", "headers": {"X-Atlassian-Webhook-Identifier": identifier, "X-Atlassian-Webhook-Retry": "1"},
                       "body": {"webhookEvent": "jira:issue_updated", "issue": {"id": "9", "key": key, "fields": {"updated": "2026-09-01T12:00:00.000+0000"}}}})


class FakeServiceBusReceiver:
    def __init__(self, bodies):
        self.queue = [SimpleNamespace(body=b, message_id=f"sb{i}", delivery_count=1) for i, b in enumerate(bodies)]
        self.completed, self.abandoned = [], []

    def receive_messages(self, max_message_count, max_wait_time):
        out, self.queue = self.queue[:max_message_count], self.queue[max_message_count:]
        return out

    def complete_message(self, msg):
        self.completed.append(msg.message_id)

    def abandon_message(self, msg):
        self.abandoned.append(msg.message_id)


class FakePubSub:
    def __init__(self, bodies):
        self.pending = [SimpleNamespace(ack_id=f"ack{i}", delivery_attempt=1, message=SimpleNamespace(data=b.encode(), message_id=f"ps{i}")) for i, b in enumerate(bodies)]
        self.acked, self.deadlines = [], []

    def pull(self, subscription, max_messages):
        out, self.pending = self.pending[:max_messages], self.pending[max_messages:]
        return SimpleNamespace(received_messages=out)

    def acknowledge(self, subscription, ack_ids):
        self.acked += ack_ids

    def modify_ack_deadline(self, subscription, ack_ids, ack_deadline_seconds):
        self.deadlines.append((tuple(ack_ids), ack_deadline_seconds))


def cloudflare_http(bodies):
    state = {"messages": [{"id": f"cf{i}", "lease_id": f"lease{i}", "body": b, "attempts": 1} for i, b in enumerate(bodies)], "acks": [], "retries": [], "requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append((request.method, request.url.path, request.headers.get("authorization"), json.loads(request.content) if request.content else None))
        if request.url.path.endswith("/messages/pull"):
            batch, state["messages"] = state["messages"], []
            return httpx.Response(200, json={"success": True, "result": {"messages": batch}})
        if request.url.path.endswith("/messages/ack"):
            body = json.loads(request.content)
            state["acks"] += [a["lease_id"] for a in body["acks"]]
            state["retries"] += [r["lease_id"] for r in body["retries"]]
            return httpx.Response(200, json={"success": True})
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), state


def broker_http(bodies, token="brk"):
    state = {"messages": [{"id": f"b{i}", "body": b} for i, b in enumerate(bodies)], "acked": [], "nacked": [], "auth": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["auth"].append(request.headers.get("authorization"))
        if request.method == "GET" and request.url.path == "/pull":
            batch, state["messages"] = state["messages"], []
            return httpx.Response(200, json={"messages": batch})
        if request.method == "POST" and request.url.path == "/ack":
            body = json.loads(request.content)
            state["acked"] += body.get("ids", [])
            state["nacked"] += body.get("nack", [])
            return httpx.Response(200)
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), state
