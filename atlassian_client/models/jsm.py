"""Jira Service Management models."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from .common import AtlassianModel, UserRef


class JsmInfo(AtlassianModel):
    version: str | None = None
    platform_version: str | None = None
    cloud: bool | None = None


class ServiceDesk(AtlassianModel):
    id: str
    project_id: str | None = None
    project_key: str | None = None
    project_name: str | None = None


class RequestType(AtlassianModel):
    id: str
    name: str
    description: str | None = None
    service_desk_id: str | None = None


class CustomerRequest(AtlassianModel):
    issue_id: str
    issue_key: str
    request_type: str | None = None
    service_desk_id: str | None = None
    status: str | None = None
    reporter: UserRef | None = None
    created: datetime | None = None
    summary: str | None = None
    description: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)


class RequestComment(AtlassianModel):
    id: str
    author: UserRef | None = None
    body: str | None = None
    public: bool = True
    created: datetime | None = None


class SlaInfo(AtlassianModel):
    id: str
    name: str
    ongoing: bool | None = None
    breached: bool | None = None
    paused: bool | None = None
    remaining_millis: int | None = None
    goal_duration_millis: int | None = None


class KbArticle(AtlassianModel):
    title: str
    excerpt: str | None = None
    url: str | None = None
    source: Any = None


class Organization(AtlassianModel):
    id: str
    name: str
