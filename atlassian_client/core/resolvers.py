"""Name -> id resolvers so agents use names while the wire uses ids (design section 7.8)."""
from __future__ import annotations

import re
from typing import Any

from ..models.confluence import Space
from ..models.jira import Field
from ..protocols.confluence import ConfluenceSpaceReader
from ..protocols.jira import JiraMetadataReader, JiraUserDirectory
from .cache import MetadataCache
from .errors import AmbiguousReferenceError, NotFoundError

_FIELD_ID = re.compile(r"^(customfield_\d+|[a-z][a-zA-Z]*)$")
_ACCOUNT_ID = re.compile(r"^([0-9a-f]{24}|\d+:[0-9a-f-]{36})$")


class FieldResolver:
    """Maps ``"Story Points"`` -> ``customfield_10016``. Ambiguous names raise with candidates listed."""

    def __init__(self, reader: JiraMetadataReader, cache: MetadataCache | None = None, scope: str = "default") -> None:
        self._reader, self._cache, self._scope = reader, cache or MetadataCache(), scope

    async def _fields(self) -> list[Field]:
        return await self._cache.get_or_load(self._reader.list_fields, self._scope, "jira", "fields")

    async def resolve(self, name_or_id: str) -> str:
        fields = await self._fields()
        by_id = {f.id: f for f in fields}
        if name_or_id in by_id:
            return name_or_id
        matches = [f for f in fields if f.name.lower() == name_or_id.lower()]
        if len(matches) == 1:
            return matches[0].id
        if len(matches) > 1:
            raise AmbiguousReferenceError(
                f"field name {name_or_id!r} matches {len(matches)} fields",
                hint="Use the field id instead of the name.",
                details={"candidates": [{"id": f.id, "name": f.name, "custom": f.custom} for f in matches]})
        if _FIELD_ID.match(name_or_id):
            return name_or_id                 # system field ids such as "summary" that the list did not return
        raise NotFoundError(f"unknown field {name_or_id!r}", hint="Call jira_list_fields to see valid field names and ids.")

    async def resolve_many(self, names: list[str]) -> list[str]:
        return [await self.resolve(n) for n in names]

    async def names_by_id(self) -> dict[str, str]:
        return {f.id: f.name for f in await self._fields()}

    async def resolve_mapping(self, values: dict[str, Any]) -> dict[str, Any]:
        return {await self.resolve(k): v for k, v in values.items()}


class UserResolver:
    """Maps email or display name -> ``accountId`` (Cloud). Account ids are preferred over mutable names."""

    def __init__(self, directory: JiraUserDirectory, cache: MetadataCache | None = None, scope: str = "default") -> None:
        self._dir, self._cache, self._scope = directory, cache or MetadataCache(), scope

    async def resolve(self, ref: str) -> str:
        if _ACCOUNT_ID.match(ref):
            return ref
        cached = self._cache.get(self._scope, "jira", "user", ref.lower())
        if cached:
            return str(cached)
        users = await self._dir.search_users(ref, limit=10)
        exact = [u for u in users if ref.lower() in ((u.email or "").lower(), (u.display_name or "").lower())]
        pool = exact or users
        if not pool:
            raise NotFoundError(f"no user matches {ref!r}", hint="Try the full display name or the email address.")
        if len(pool) > 1:
            raise AmbiguousReferenceError(f"{len(pool)} users match {ref!r}", hint="Use the accountId or the email.",
                                          details={"candidates": [{"account_id": u.account_id, "name": u.display_name}
                                                                  for u in pool]})
        account_id = pool[0].account_id or ""
        self._cache.set(account_id, self._scope, "jira", "user", ref.lower())
        return account_id


class SpaceResolver:
    """Maps a Confluence space key <-> space id (v2 needs ids; humans use keys)."""

    def __init__(self, reader: ConfluenceSpaceReader, cache: MetadataCache | None = None, scope: str = "default") -> None:
        self._reader, self._cache, self._scope = reader, cache or MetadataCache(), scope

    async def _space(self, key_or_id: str) -> Space:
        cached = self._cache.get(self._scope, "confluence", "space", key_or_id)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        space = await self._reader.get_space(key_or_id)
        for k in (space.key, space.id):
            self._cache.set(space, self._scope, "confluence", "space", k)
        return space

    async def to_id(self, key_or_id: str) -> str:
        return key_or_id if key_or_id.isdigit() else (await self._space(key_or_id)).id

    async def to_key(self, key_or_id: str) -> str:
        return (await self._space(key_or_id)).key if key_or_id.isdigit() else key_or_id
