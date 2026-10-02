"""Organization audit models."""
from __future__ import annotations

from datetime import datetime
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
