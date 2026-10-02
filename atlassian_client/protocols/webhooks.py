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
class WebhookManager(Protocol):
    async def list_webhooks(self) -> list[WebhookRegistration]: ...
    async def register(self, url: str, subscriptions: Sequence[WebhookSubscription], *,
                       secret: str | None = None) -> list[WebhookRegistration]: ...
    async def delete(self, webhook_ids: Sequence[str]) -> None: ...
    async def refresh(self, webhook_ids: Sequence[str]) -> datetime | None: ...  # dynamic only (30-day expiry)
    async def list_failed(self, *, after: datetime | None = None) -> list[FailedWebhook]: ...
