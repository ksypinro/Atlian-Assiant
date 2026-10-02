"""Access policy data model (design section 16.4). The PolicyGuard enforcing it is added in P1.AGENT.002."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from .capabilities import Access, BackendKind, Operation

if TYPE_CHECKING:
    from .config import AtlassianConfig


@dataclass(frozen=True)
class AccessPolicy:
    allowed: frozenset[Access] = frozenset({Access.READ, Access.SEARCH})
    confirm: frozenset[Access] = frozenset({Access.WRITE, Access.DELETE, Access.MANAGE})
    allow_credit_consuming: bool = False
    allowed_cloud_ids: frozenset[str] | None = None
    allowed_projects: frozenset[str] | None = None
    allowed_spaces: frozenset[str] | None = None
    max_results_per_call: int = 100
    max_attachment_bytes: int = 10 * 1024 * 1024
    allow_browser_writes: bool = False
    allow_direct_db: bool = False

    @classmethod
    def read_only(cls) -> AccessPolicy:
        return cls()

    @classmethod
    def read_write(cls) -> AccessPolicy:
        return cls(allowed=frozenset({Access.READ, Access.SEARCH, Access.WRITE}))

    @classmethod
    def analytics(cls) -> AccessPolicy:
        return cls(allowed=frozenset({Access.READ, Access.SEARCH, Access.ANALYTICS}))

    @classmethod
    def from_config(cls, config: AtlassianConfig) -> AccessPolicy:
        return cls(allowed=frozenset(config.access), allow_credit_consuming=config.allow_credit_consuming,
                   allow_browser_writes=config.allow_browser_writes, allow_direct_db=config.allow_direct_db)


@dataclass(frozen=True)
class PlannedAction:
    """What a write WOULD do. Returned by ``dry_run=True`` and shown to the confirmer."""

    operation: Operation
    target: str
    summary: str
    arguments: Mapping[str, Any]
    preferred_backend: BackendKind | None = None
    reversible: bool = True
    warnings: tuple[str, ...] = field(default_factory=tuple)


class ConfirmationHandler(Protocol):
    async def confirm(self, action: PlannedAction) -> bool: ...
