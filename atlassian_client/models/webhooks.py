"""Webhook and raw-request models."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from .common import AtlassianModel


class WebhookSubscription(AtlassianModel):
    events: list[str]
    jql_filter: str | None = None
    field_ids_filter: list[str] = Field(default_factory=list)


class WebhookRegistration(AtlassianModel):
    id: str
    url: str | None = None
    events: list[str] = Field(default_factory=list)
    jql_filter: str | None = None
    expires_at: datetime | None = None
    enabled: bool = True


class FailedWebhook(AtlassianModel):
    id: str
    url: str | None = None
    body: str | None = None
    failure_time: datetime | None = None


class RawResponse(AtlassianModel):
    status: int
    headers: dict[str, str] = Field(default_factory=dict)
    json_body: Any = None
    text: str | None = None
