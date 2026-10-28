"""JiraWebhookReceiver: DM-01 / DM-02 / DM-03 (and DM-47 shape) push endpoint."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from ..model import ChangeEvent, EventSourceKind
from ..normalize import normalize_jira_webhook
from ..stores.base import DedupStore, LocalQueue
from .base import InboundRequest, QueueEndpoint, RequestVerifier


class JiraWebhookReceiver(QueueEndpoint):
    """Receives Jira Cloud webhooks (admin UI, admin REST and dynamic registrations) and enqueues normalized events.

    Dedup key: ``X-Atlassian-Webhook-Identifier`` (stable across retries). ``X-Atlassian-Webhook-Retry`` / ``-Flow`` are kept on
    ``event.delivery``; Primary and Secondary flows are tracked as separate latency series (``latency_by_flow``).
    """

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-01", "DM-02", "DM-03", "DM-44"})        # DM-44: the same receiver behind a tunnel
    requires_public_ingress: ClassVar[bool] = True
    kind: ClassVar[EventSourceKind] = EventSourceKind.JIRA_WEBHOOK

    def __init__(self, queue: LocalQueue, *, path: str = "/hooks/jira", verifiers: Sequence[RequestVerifier] = (),
                 receipts: DedupStore | None = None, method_id: str = "DM-03", **kwargs: Any) -> None:
        super().__init__(queue, path=path, verifiers=verifiers, receipts=receipts, **kwargs)
        self.method_id = method_id

    @classmethod
    def from_env(cls, queue: LocalQueue | None = None, *, path: str = "/hooks/jira", method_id: str = "DM-03", env: Mapping[str, str] | None = None) -> JiraWebhookReceiver:
        """Configure the verifier chain from the environment (relays and serverless handlers): ``ATLASSIAN_EVENTS_WEBHOOK_SECRET`` (HMAC),
        ``ATLASSIAN_EVENTS_WEBHOOK_HEADER`` / ``ATLASSIAN_EVENTS_WEBHOOK_TOKEN`` (static header), ``ATLASSIAN_EVENTS_ALLOWED_IPS`` (comma-separated CIDRs)."""
        import os

        from ..stores.memory import MemoryLocalQueue
        from .base import HmacSignatureVerifier, IpAllowlistVerifier, StaticHeaderVerifier
        e = env if env is not None else os.environ
        verifiers: list[RequestVerifier] = []
        if e.get("ATLASSIAN_EVENTS_ALLOWED_IPS"):
            verifiers.append(IpAllowlistVerifier([c.strip() for c in e["ATLASSIAN_EVENTS_ALLOWED_IPS"].split(",") if c.strip()]))
        if e.get("ATLASSIAN_EVENTS_WEBHOOK_SECRET"):
            verifiers.append(HmacSignatureVerifier(e["ATLASSIAN_EVENTS_WEBHOOK_SECRET"], header=e.get("ATLASSIAN_EVENTS_SIGNATURE_HEADER", "X-Hub-Signature")))
        if e.get("ATLASSIAN_EVENTS_WEBHOOK_TOKEN"):
            verifiers.append(StaticHeaderVerifier(e.get("ATLASSIAN_EVENTS_WEBHOOK_HEADER", "X-Webhook-Token"), e["ATLASSIAN_EVENTS_WEBHOOK_TOKEN"]))
        return cls(queue or MemoryLocalQueue(), path=path, verifiers=verifiers, method_id=method_id)

    def normalize(self, request: InboundRequest, payload: Mapping[str, Any]) -> ChangeEvent:
        return normalize_jira_webhook(payload, request.headers, method_id=self.method_id, body=request.body, received_at=self._clock(),
                                      verified=bool(self.verifiers), source=self.kind)
