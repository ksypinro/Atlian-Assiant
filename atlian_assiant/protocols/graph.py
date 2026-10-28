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
class TeamworkGraphReader(Protocol):
    async def get_context(self, ari_or_url: str, *, max_items: int = 50) -> GraphContext: ...
    async def get_objects(self, aris_or_urls: Sequence[str]) -> list[GraphObject]: ...


@runtime_checkable
class TeamworkGraphWriter(Protocol):
    """One typed Teamwork Graph relationship, e.g. ``jira-work-item-links-jira-work-item`` or ``jira-work-item-tracks-atlassian-project``."""

    async def add_relationship(self, ari_or_url: str, relationship_type: str, target: str, *, title: str | None = None) -> None: ...


@runtime_checkable
class SemanticSearcher(Protocol):
    async def semantic_search(self, query: str, *, products: Sequence[str] = (), limit: int = 20) -> list[SemanticHit]: ...


@runtime_checkable
class GraphQLExecutor(Protocol):
    async def execute(self, query: str, variables: Mapping[str, Any] | None = None, *,
                      experimental_apis: Sequence[str] = (), operation_name: str | None = None) -> dict[str, Any]: ...
