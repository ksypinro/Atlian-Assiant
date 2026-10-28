"""ForgeRemoteReceiver (DM-09): the Python backend that Forge Remote invokes. Verifies the Forge Invocation Token (FIT) before anything else."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from ..model import ChangeEvent, EventSourceKind
from ..normalize import forge_change_event
from ..stores.base import DedupStore, LocalQueue
from .base import ForgeInvocationTokenVerifier, InboundRequest, KeySource, QueueEndpoint, RequestVerifier

FORGE_JWKS_URL = "https://forge.cdn.prod.atlassian-dev.net/.well-known/jwks.json"      # location unverified: VER-24, pass your own


class ForgeRemoteReceiver(QueueEndpoint):
    """Receives ``{eventType, event, installContext}`` from the generated remote-mode app. The event id is ``event.id`` (else a body hash)."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-08", "DM-09"})
    requires_public_ingress: ClassVar[bool] = True
    kind: ClassVar[EventSourceKind] = EventSourceKind.FORGE_REMOTE

    def __init__(self, queue: LocalQueue, *, app_id: str, key_source: KeySource, path: str = "/hooks/forge", jwks_url: str = FORGE_JWKS_URL,
                 extra_verifiers: Sequence[RequestVerifier] = (), receipts: DedupStore | None = None, **kwargs: Any) -> None:
        verifiers = (ForgeInvocationTokenVerifier(app_id, jwks_url, key_source), *extra_verifiers)
        super().__init__(queue, path=path, verifiers=verifiers, receipts=receipts, **kwargs)

    def normalize(self, request: InboundRequest, payload: Mapping[str, Any]) -> ChangeEvent:
        inner = payload.get("event") or {}
        entity = inner.get("issue") or inner.get("content") or inner.get("page") or {}
        record: dict[str, Any] = {"eventType": payload.get("eventType") or inner.get("eventType"), "entity": {"id": entity.get("id"), "key": entity.get("key")}}
        event_id = inner.get("id") or payload.get("id")
        if event_id:
            record["id"] = event_id
        else:
            record["entity"]["body"] = dict(payload)                         # no id given: forge_change_event derives a stable hash
        return forge_change_event(record, source=self.kind, method_id="DM-09", received_at=self._clock(), verified=True)
