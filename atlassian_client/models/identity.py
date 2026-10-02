"""Identity models."""
from __future__ import annotations

from pydantic import Field

from .common import AtlassianModel


class AccessibleResource(AtlassianModel):
    id: str                               # cloudId
    name: str | None = None
    url: str | None = None
    scopes: list[str] = Field(default_factory=list)


class Identity(AtlassianModel):
    account_id: str | None = None
    display_name: str | None = None
    email: str | None = None
    site: str | None = None
    cloud_id: str | None = None
    auth_scheme: str | None = None
    backend: str | None = None
