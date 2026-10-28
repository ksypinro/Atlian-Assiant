"""Data Center webhook receivers (DM-46 Confluence DC, DM-47 Jira DC): HTTPS/HTTP push inside the LAN."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from ...capabilities import Product
from ..model import ChangeEvent, DeliveryInfo, EntityType, EventSourceKind
from ..normalize import _hash, _ms_to_dt, classify_confluence_dc_event, normalize_jira_webhook
from ..stores.base import DedupStore, LocalQueue
from .base import InboundRequest, QueueEndpoint, RequestVerifier


class JiraDCWebhookReceiver(QueueEndpoint):
    """Jira DC sends the same payload family as the Cloud admin webhooks. Dedup key: the delivery id header when present, else a payload hash."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-47"})
    requires_public_ingress: ClassVar[bool] = False
    kind: ClassVar[EventSourceKind] = EventSourceKind.JIRA_DC_WEBHOOK

    def __init__(self, queue: LocalQueue, *, path: str = "/hooks/jira-dc", verifiers: Sequence[RequestVerifier] = (), receipts: DedupStore | None = None, **kwargs: Any) -> None:
        super().__init__(queue, path=path, verifiers=verifiers, receipts=receipts, **kwargs)

    def normalize(self, request: InboundRequest, payload: Mapping[str, Any]) -> ChangeEvent:
        ev = normalize_jira_webhook(payload, request.headers, method_id="DM-47", body=request.body, received_at=self._clock(), verified=bool(self.verifiers), source=self.kind)
        delivery_id = request.header("X-Request-Id") or request.header("X-Atlassian-Webhook-Identifier")
        return ev.model_copy(update={"event_id": delivery_id or ev.event_id})


class ConfluenceDCWebhookReceiver(QueueEndpoint):
    """Confluence DC payloads: ``{"event": "page_created", "timestamp": ms, "page": {...}|"space": {...}|"comment": {...}, "userKey": ...}``."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-46"})
    requires_public_ingress: ClassVar[bool] = False
    kind: ClassVar[EventSourceKind] = EventSourceKind.CONFLUENCE_DC_WEBHOOK

    def __init__(self, queue: LocalQueue, *, path: str = "/hooks/confluence-dc", verifiers: Sequence[RequestVerifier] = (), receipts: DedupStore | None = None, **kwargs: Any) -> None:
        super().__init__(queue, path=path, verifiers=verifiers, receipts=receipts, **kwargs)

    def normalize(self, request: InboundRequest, payload: Mapping[str, Any]) -> ChangeEvent:
        native = str(payload.get("event") or "unknown")
        entity, action = classify_confluence_dc_event(native)
        sub: Mapping[str, Any] = payload.get("page") or payload.get("blog") or payload.get("comment") or payload.get("space") or payload.get("attachment") or {}
        delivery_id = request.header("X-Request-Id")
        entity_id = sub.get("id")
        space = sub.get("space")
        container = sub.get("key") if entity is EntityType.SPACE else (sub.get("spaceKey") or (space.get("key") if isinstance(space, Mapping) else None))
        return ChangeEvent(event_id=delivery_id or _hash(request.body), source=self.kind, product=Product.CONFLUENCE, entity_type=entity, action=action, native_type=native,
                           entity_id=None if entity_id is None else str(entity_id), entity_key=sub.get("title") or sub.get("key"), container_key=container,
                           entity_version=None if sub.get("version") is None else str(sub.get("version")), occurred_at=_ms_to_dt(payload.get("timestamp")),
                           received_at=self._clock(), actor_account_id=payload.get("userKey"),
                           delivery=DeliveryInfo(method_id="DM-46", transport_id=delivery_id, verified=bool(self.verifiers), payload_bytes=len(request.body)),
                           payload=json.loads(request.body))
