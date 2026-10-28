"""Protocols (design section 9.1)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

from ..capabilities import BackendKind, Deployment


@dataclass(frozen=True)
class BackendHealth:
    status: Literal["ready", "degraded", "unavailable"]
    detail: str | None = None


@runtime_checkable
class Backend(Protocol):
    kind: ClassVar[BackendKind]
    deployments: ClassVar[frozenset[Deployment]]

    def supported_operations(self) -> frozenset[str]:
        """Operation ids this instance can serve right now (may shrink after probing)."""
        ...

    async def health(self) -> BackendHealth: ...
    async def aclose(self) -> None: ...


def implementation_of(backend: object, protocol: type) -> Any:
    """Return the object that implements ``protocol`` for ``backend``, or None.

    A backend may expose several protocol implementations through ``implementation_for(protocol)`` (composition). This is
    needed because Jira and Confluence protocols share method names with incompatible signatures
    (``list_comments``, ``list_versions``, ``list_attachments``, ``download_attachment``), so a single object cannot
    implement both. Backends that do not define the hook are checked structurally as before.
    """
    provider = getattr(backend, "implementation_for", None)
    if callable(provider):
        found = provider(protocol)
        if found is not None:
            return found
    return backend if isinstance(backend, protocol) else None
