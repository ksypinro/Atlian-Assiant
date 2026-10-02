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
class JiraExporter(Protocol):
    async def export_issues_csv(self, jql: str, *, fields: Sequence[str] | None = None, dest: Path) -> Path: ...


@runtime_checkable
class ConfluenceExporter(Protocol):
    async def export_content(self, content_id: str, *, format: Literal["pdf", "word", "html", "markdown"],
                             dest: Path) -> Path: ...
    async def export_space(self, space_key: str, *, format: Literal["pdf", "html", "xml", "csv"], dest: Path) -> Path: ...
