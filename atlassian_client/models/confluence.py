"""Confluence models. ``Content`` normalizes v2 body formats into Markdown, storage and ADF."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from .common import AtlassianModel, UserRef


class Content(AtlassianModel):
    id: str
    type: str = "page"                    # page | blogpost | whiteboard | database | folder | embed | custom
    title: str | None = None
    status: str | None = None
    space_id: str | None = None
    space_key: str | None = None
    parent_id: str | None = None
    version: int | None = None
    author_id: str | None = None
    created: datetime | None = None
    updated: datetime | None = None
    url: str | None = None
    body_markdown: str | None = None
    body_storage: str | None = Field(default=None, repr=False)
    body_adf: dict[str, Any] | None = Field(default=None, repr=False)
    labels: list[str] = Field(default_factory=list)


class ContentVersion(AtlassianModel):
    number: int
    message: str | None = None
    author_id: str | None = None
    created: datetime | None = None
    minor_edit: bool | None = None


class ContentDiff(AtlassianModel):
    content_id: str
    from_version: int
    to_version: int
    unified: str


class ContentDraft(AtlassianModel):
    type: str = "page"
    title: str
    space: str                            # space key or id
    body: str = ""                        # Markdown by default
    parent_id: str | None = None
    status: str = "current"


class ContentUpdate(AtlassianModel):
    title: str | None = None
    body: str | None = None
    version: int | None = None            # current version; the client sends version + 1
    message: str | None = None
    status: str | None = None


class ConfluenceComment(AtlassianModel):
    id: str
    kind: str = "footer"                  # footer | inline
    body_markdown: str | None = None
    author_id: str | None = None
    created: datetime | None = None
    resolved: bool | None = None
    parent_id: str | None = None


class Attachment(AtlassianModel):
    id: str
    title: str
    media_type: str | None = None
    file_size: int | None = None
    page_id: str | None = None
    download_url: str | None = None
    version: int | None = None


class AttachmentContent(AtlassianModel):
    attachment_id: str
    filename: str | None = None
    mime_type: str | None = None
    size: int = 0
    data: bytes | None = Field(default=None, exclude=True, repr=False)
    path: str | None = None
    text: str | None = None


class Label(AtlassianModel):
    id: str | None = None
    name: str
    prefix: str | None = None


class Space(AtlassianModel):
    id: str
    key: str
    name: str | None = None
    type: str | None = None
    status: str | None = None
    homepage_id: str | None = None


class Restrictions(AtlassianModel):
    read_users: list[UserRef] = Field(default_factory=list)
    read_groups: list[str] = Field(default_factory=list)
    update_users: list[UserRef] = Field(default_factory=list)
    update_groups: list[str] = Field(default_factory=list)

    @property
    def restricted(self) -> bool:
        return bool(self.read_users or self.read_groups or self.update_users or self.update_groups)


class Permissions(AtlassianModel):
    operations: list[dict[str, Any]] = Field(default_factory=list)


class SearchHit(AtlassianModel):
    content_id: str | None = None
    type: str | None = None
    title: str | None = None
    excerpt: str | None = None
    space_key: str | None = None
    url: str | None = None
    last_modified: datetime | None = None


class Task(AtlassianModel):
    id: str
    status: str                           # complete | incomplete
    body: str | None = None
    content_id: str | None = None
    due: str | None = None
    assignee_id: str | None = None


class Template(AtlassianModel):
    id: str
    name: str
    description: str | None = None
    space_key: str | None = None
