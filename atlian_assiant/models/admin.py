"""Organization audit models."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from .common import AtlassianModel


class AuditEvent(AtlassianModel):
    id: str
    action: str | None = None
    actor_id: str | None = None
    occurred_at: datetime | None = None
    processed_at: datetime | None = None
    context: list[dict[str, Any]] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)


class AuditEventPage(AtlassianModel):
    events: list[AuditEvent] = Field(default_factory=list)
    next_cursor: str | None = None


def _dt(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def audit_event_from_api(raw: Mapping[str, Any]) -> AuditEvent:
    """Tolerant mapping of an events / events-stream item or an audit-webhook body (JSON:API ``attributes`` or flat); unknown fields are kept."""
    attrs = dict(raw.get("attributes") or raw)
    actor = attrs.get("actor") or {}
    return AuditEvent(id=str(raw.get("id") or attrs.get("id")), action=attrs.get("action"), actor_id=actor.get("id") if isinstance(actor, Mapping) else None,
                      occurred_at=_dt(attrs.get("time") or attrs.get("occurredAt")), processed_at=_dt(attrs.get("processedAt") or attrs.get("processed_at")),
                      context=list(attrs.get("context") or []), attributes={k: v for k, v in attrs.items() if k not in ("actor", "context", "time", "action")},
                      raw=dict(raw))
