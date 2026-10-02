"""Models shared by every product (design section 8)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from ..capabilities import BackendKind
from ..formats.render import truncate

T = TypeVar("T")


class SourceRef(BaseModel):
    backend: BackendKind
    site: str | None = None
    cloud_id: str | None = None
    url: str | None = None                # browser URL of the object
    fetched_at: datetime


class AtlassianModel(BaseModel):
    """Base for all domain models: tolerant of new API fields, keeps the raw payload (excluded from dumps)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    source: SourceRef | None = None
    raw: dict[str, Any] | None = Field(default=None, exclude=True, repr=False)

    def to_agent_text(self, max_chars: int = 4000) -> str:
        """Compact Markdown rendering for LLM context windows, truncated with an explicit marker."""
        data = self.model_dump(exclude={"source", "raw"}, exclude_none=True, exclude_defaults=True, mode="json")
        head = next((str(data[k]) for k in ("key", "title", "name", "id") if k in data), type(self).__name__)
        lines = [f"**{type(self).__name__}** {head}"]
        for k, v in data.items():
            if v in ("", [], {}):
                continue
            lines.append(f"- {k}: {v}")
        return truncate("\n".join(lines), max_chars)[0]


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None        # opaque; pass back unchanged
    total: int | None = None              # only when the API reports it
    source: SourceRef | None = None

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None


class UserRef(AtlassianModel):
    account_id: str | None = None         # Cloud
    username: str | None = None           # Data Center
    display_name: str | None = None
    email: str | None = None              # often hidden by privacy settings
    active: bool | None = None


class Tombstone(AtlassianModel):
    """Returned by enrichment when the entity no longer exists (deleted or permission lost)."""

    entity_type: str
    entity_id: str
    reason: str = "not_found"


class BodyInput(BaseModel):
    """Rich text for writes. A plain ``str`` passed to any write method means Markdown."""

    format: Literal["markdown", "plain", "adf", "storage", "wiki"] = "markdown"
    value: str | dict[str, Any]
