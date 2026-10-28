"""Mirror / sync engine (design 15)."""
from __future__ import annotations

from typing import Any

from .base import TOMBSTONE, MirrorStore, MirrorVersionStore
from .confluence import ConfluenceMirror, ConfluenceReconcileResult
from .jira import JiraMirror, ReconcileResult, SyncResult


class MirrorClient:
    """``client.mirror``: build a mirror bound to this client (and therefore to its policy and routing)."""

    __namespace__ = False

    def __init__(self, client: Any) -> None:
        self._client = client

    def jira(self, store: MirrorStore, *, jql: str, scope: str = "default", **kwargs: Any) -> JiraMirror:
        """A JiraMirror over the issues matching ``jql``, persisted in ``store``."""
        return JiraMirror(self._client, store, jql=jql, scope=scope, **kwargs)

    def confluence(self, store: MirrorStore, *, cql: str, scope: str = "default", **kwargs: Any) -> ConfluenceMirror:
        """A ConfluenceMirror over the content matching ``cql``, persisted in ``store`` (restrictions included)."""
        return ConfluenceMirror(self._client, store, cql=cql, scope=scope, **kwargs)


__all__ = ["TOMBSTONE", "ConfluenceMirror", "ConfluenceReconcileResult", "JiraMirror", "MirrorClient", "MirrorStore", "MirrorVersionStore", "ReconcileResult", "SyncResult"]
