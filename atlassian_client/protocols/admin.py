# ruff: noqa: F401
from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from ..models.admin import AuditEvent, AuditEventPage
from ..models.common import Page
from ..models.graph import GraphContext, GraphObject, SemanticHit
from ..models.identity import AccessibleResource, Identity
from ..models.webhooks import FailedWebhook, RawResponse, WebhookRegistration, WebhookSubscription


@runtime_checkable
class OrgAuditReader(Protocol):
    async def list_events(self, *, start: datetime | None = None, end: datetime | None = None,
                          action: str | None = None, query: str | None = None, cursor: str | None = None,
                          limit: int = 200) -> Page[AuditEvent]: ...
    async def get_event(self, event_id: str) -> AuditEvent: ...
    async def list_event_actions(self) -> list[str]: ...
    async def poll_stream(self, *, cursor: str | None = None, start: datetime | None = None,
                          limit: int = 500, sort_order: Literal["asc", "desc"] = "asc") -> AuditEventPage: ...
