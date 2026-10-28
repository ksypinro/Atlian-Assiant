"""Delta Sharing access (design 10.9): ``ShareClient`` is the small surface the backend needs; ``DeltaSharingClient`` implements it with the optional
``delta-sharing`` package and the profile file. Tests use an in-memory client."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from ...core.errors import BackendUnavailableError, NotFoundError


class ShareClient(Protocol):
    def list_shares(self) -> list[str]: ...
    def list_tables(self, share: str | None = None) -> list[str]: ...
    def load(self, table: str, columns: Sequence[str] | None = None, limit: int | None = None) -> list[dict[str, Any]]: ...


class DeltaSharingClient:
    """``delta_sharing`` is imported lazily; ``profile`` is the share profile JSON a data steward provides (it contains a bearer token: never logged)."""

    def __init__(self, profile: Path) -> None:
        self.profile = Path(profile)
        self._client: Any = None

    def _sc(self) -> Any:
        if self._client is None:
            try:
                import delta_sharing
            except ImportError as exc:
                raise BackendUnavailableError("delta-sharing is not installed", hint="pip install 'atlian-assiant[datalake]'") from exc
            if not self.profile.is_file():
                raise BackendUnavailableError(f"the share profile {self.profile} does not exist", hint="Set ATLASSIAN_DELTA_SHARING_PROFILE to the profile file from your Atlassian data steward.")
            self._client = delta_sharing.SharingClient(str(self.profile))
        return self._client

    def list_shares(self) -> list[str]:
        return [s.name for s in self._sc().list_shares()]

    def list_tables(self, share: str | None = None) -> list[str]:
        return [f"{t.share}.{t.schema}.{t.name}" for t in self._sc().list_all_tables() if share in (None, t.share)]

    def load(self, table: str, columns: Sequence[str] | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        import delta_sharing

        if table not in self.list_tables():
            raise NotFoundError(f"table {table!r} is not in the share", hint="Call list_tables() for the exact share.schema.table names.")
        frame = delta_sharing.load_as_pandas(f"{self.profile}#{table}", limit=limit)
        if columns:
            frame = frame[list(columns)]
        return [dict(r) for r in frame.to_dict(orient="records")]


class InMemoryShareClient:
    """A share held in memory: the test double, and a convenient way to run the backend over exported tables."""

    def __init__(self, tables: Mapping[str, Sequence[Mapping[str, Any]]], *, share: str = "atlassian") -> None:
        self.share = share
        self.data = {f"{share}.default.{name}": [dict(r) for r in rows] for name, rows in tables.items()}
        self.loads: list[str] = []

    def list_shares(self) -> list[str]:
        return [self.share]

    def list_tables(self, share: str | None = None) -> list[str]:
        return [t for t in self.data if share in (None, t.split(".")[0])]

    def load(self, table: str, columns: Sequence[str] | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        if table not in self.data:
            raise NotFoundError(f"table {table!r} is not in the share", hint="Call list_tables() for the exact share.schema.table names.")
        self.loads.append(table)
        rows = self.data[table][:limit]
        return [{c: r.get(c) for c in columns} if columns else dict(r) for r in rows]
