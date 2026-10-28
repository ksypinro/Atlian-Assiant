"""AutomationWebRequestReceiver: DM-15 (Jira) and DM-16 (Confluence) 'Send web request' target."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from ..model import ChangeEvent, EventSourceKind
from ..normalize import normalize_automation_envelope
from ..stores.base import DedupStore, LocalQueue
from .base import InboundRequest, QueueEndpoint, RequestVerifier


class AutomationWebRequestReceiver(QueueEndpoint):
    """Receives the ``STANDARD_ENVELOPE`` posted by an Automation rule. Dedup key: the envelope's ``atl_event_id``.

    Automation web requests carry no signature: authenticate them with a ``StaticHeaderVerifier`` (custom header configured on
    the rule) and, where possible, an ``IpAllowlistVerifier``.
    """

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-15", "DM-16"})
    requires_public_ingress: ClassVar[bool] = True
    kind: ClassVar[EventSourceKind] = EventSourceKind.AUTOMATION_WEB_REQUEST

    def __init__(self, queue: LocalQueue, *, path: str = "/hooks/automation", verifiers: Sequence[RequestVerifier] = (),
                 receipts: DedupStore | None = None, method_id: str = "DM-15", **kwargs: Any) -> None:
        super().__init__(queue, path=path, verifiers=verifiers, receipts=receipts, **kwargs)
        self.method_id = method_id

    def normalize(self, request: InboundRequest, payload: Mapping[str, Any]) -> ChangeEvent:
        method = "DM-16" if payload.get("product") == "confluence" else self.method_id
        return normalize_automation_envelope(payload, request.headers, method_id=method, body=request.body, received_at=self._clock(),
                                             verified=bool(self.verifiers))
