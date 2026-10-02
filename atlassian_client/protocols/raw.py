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
class RawRequester(Protocol):
    async def request(self, method: str, path: str, *, product: Literal["jira", "confluence", "jsm", "agile", "admin"],
                      params: Mapping[str, Any] | None = None, json: Any = None,
                      headers: Mapping[str, str] | None = None) -> RawResponse: ...
    def paginate(self, method: str, path: str, *, product: str, strategy: str,
                 params: Mapping[str, Any] | None = None, json: Any = None) -> AsyncIterator[dict[str, Any]]: ...
