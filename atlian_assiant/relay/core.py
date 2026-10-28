"""Shared core of the managed relays (design 14.8, EV section 42): the SAME verifier chain as a local receiver, minimal payloads, no state."""
from __future__ import annotations

import base64
import os
from collections.abc import Mapping
from typing import Any

from ..core.errors import ConfigurationError
from ..events.model import ChangeEvent
from ..events.receivers.base import InboundRequest, RelayOutcome
from ..events.receivers.jira import JiraWebhookReceiver


def build_receiver(env: Mapping[str, str] | None = None) -> JiraWebhookReceiver:
    """The relay's receiver, configured from environment variables / the platform's secret store (never from the request)."""
    e = env if env is not None else os.environ
    receiver = JiraWebhookReceiver.from_env(path=e.get("ATLASSIAN_EVENTS_RELAY_PATH", "/hooks/jira"), method_id=e.get("ATLASSIAN_EVENTS_RELAY_METHOD", "DM-39"), env=e)
    if not receiver.verifiers:
        raise ConfigurationError("the relay has no verifier configured and refuses to run unauthenticated",
                                 hint="Set ATLASSIAN_EVENTS_WEBHOOK_SECRET and/or ATLASSIAN_EVENTS_WEBHOOK_TOKEN (and ATLASSIAN_EVENTS_ALLOWED_IPS) as platform secrets.")
    return receiver


def decode_body(body: Any, is_base64: bool = False) -> bytes:
    if body is None:
        return b""
    if isinstance(body, bytes):
        return body
    return base64.b64decode(body) if is_base64 else str(body).encode()


def minimal(event: ChangeEvent) -> str:
    """Minimal payload (EV section 42): the raw webhook body is dropped; the consumer re-reads canonical state through REST (CC-07)."""
    return event.model_copy(update={"payload": {}}).model_dump_json()


def process(receiver: JiraWebhookReceiver, method: str, path: str, headers: Mapping[str, str], body: bytes, remote_addr: str | None = None) -> tuple[int, str | None]:
    """Verify + normalize one request. Returns ``(http_status, message_to_enqueue | None)``: 202 with a message, otherwise only the status
    (401 forged, 400 malformed, 413 too large). Relays do not dedup: duplicates are collapsed by the consumer's dedup store."""
    outcome: RelayOutcome = receiver.verify_and_normalize_sync(InboundRequest(method, path, {k.lower(): v for k, v in headers.items()}, body, None, remote_addr))
    if outcome.event is None:
        return outcome.status, None
    return 202, minimal(outcome.event)
