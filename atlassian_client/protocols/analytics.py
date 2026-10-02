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
class AnalyticsReader(Protocol):
    async def list_shares(self) -> list[str]: ...
    async def list_tables(self, share: str | None = None) -> list[str]: ...
    async def load_table(self, table: str, *, columns: Sequence[str] | None = None, limit: int | None = None,
                         as_: Literal["records", "pandas", "arrow"] = "records") -> Any: ...
    async def query(self, sql: str, *, tables: Sequence[str]) -> list[dict[str, Any]]: ...  # DuckDB over loaded tables
