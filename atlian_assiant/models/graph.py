"""Teamwork Graph and Rovo search models."""
from __future__ import annotations

from typing import Any

from pydantic import Field

from .common import AtlassianModel


class GraphObject(AtlassianModel):
    ari: str
    type: str | None = None
    title: str | None = None
    url: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphContext(AtlassianModel):
    root: str
    related: list[GraphObject] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)


class SemanticHit(AtlassianModel):
    title: str
    url: str | None = None
    product: str | None = None
    snippet: str | None = None
    score: float | None = None
