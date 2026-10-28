"""OpenSearch / Elasticsearch mirror store over plain REST (design 15): documents are searchable as they are mirrored."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

import httpx

from ...core.errors import error_for_status


class OpenSearchMirrorStore:
    def __init__(self, http: httpx.AsyncClient, base_url: str, *, index: str = "atlassian-mirror", auth: httpx.Auth | tuple[str, str] | None = None) -> None:
        self.http, self.base, self.index, self.auth = http, base_url.rstrip("/"), index, auth

    async def _req(self, method: str, path: str, *, ok: tuple[int, ...] = (200, 201), **kw: Any) -> httpx.Response:
        resp = await self.http.request(method, f"{self.base}/{self.index}{path}", auth=self.auth, **kw)
        if resp.status_code not in ok:
            raise error_for_status(resp.status_code, f"OpenSearch {method} {path} returned {resp.status_code}", backend="opensearch", details={"body": resp.text[:300]})
        return resp

    @staticmethod
    def _id(kind: str, entity_id: str) -> str:
        return quote(f"{kind}:{entity_id}", safe="")

    async def ensure_index(self) -> None:
        await self._req("PUT", "", ok=(200, 400), json={"mappings": {"properties": {"kind": {"type": "keyword"}, "id": {"type": "keyword"}, "scope": {"type": "keyword"},
                                                                                  "parent": {"type": "keyword"}, "version": {"type": "keyword"}, "deleted": {"type": "boolean"}}}})

    async def get_version(self, kind: str, entity_id: str) -> str | None:
        resp = await self._req("GET", f"/_doc/{self._id(kind, entity_id)}", ok=(200, 404))
        if resp.status_code == 404:
            return None
        src = resp.json()["_source"]
        return None if src.get("deleted") else str(src["version"])

    async def upsert(self, kind: str, entity_id: str, version: str, document: dict[str, Any]) -> None:
        await self._req("PUT", f"/_doc/{self._id(kind, entity_id)}", json={"kind": kind, "id": entity_id, "version": version, "scope": document.get("_scope"),
                                                                           "parent": document.get("_parent"), "deleted": False, "document": document})

    async def tombstone(self, kind: str, entity_id: str) -> None:
        await self._req("POST", f"/_update/{self._id(kind, entity_id)}", ok=(200, 404), json={"doc": {"deleted": True}})

    async def _search(self, filters: list[dict[str, Any]], after: str | None) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"size": 1000, "sort": [{"id": "asc"}], "_source": ["id", "kind"], "query": {"bool": {"filter": filters}}}
        if after is not None:
            body["search_after"] = [after]
        return list((await self._req("POST", "/_search", json=body)).json()["hits"]["hits"])

    async def known_ids(self, kind: str, scope: str) -> AsyncIterator[str]:
        after: str | None = None
        while True:
            hits = await self._search([{"term": {"kind": kind}}, {"term": {"scope": scope}}, {"term": {"deleted": False}}], after)
            for h in hits:
                yield str(h["_source"]["id"])
            if len(hits) < 1000:
                return
            after = str(hits[-1]["_source"]["id"])

    async def descendants(self, kind: str, entity_id: str) -> list[str]:
        out: list[str] = []
        frontier = [f"{kind}:{entity_id}"]
        while frontier:
            parent = frontier.pop()
            for h in await self._search([{"term": {"parent": parent}}, {"term": {"deleted": False}}], None):
                src = h["_source"]
                if src["id"] not in out:
                    out.append(str(src["id"]))
                    frontier.append(f"{src['kind']}:{src['id']}")
        return sorted(out)

    async def search_text(self, query: str, *, size: int = 10) -> list[dict[str, Any]]:
        """Full-text search over the mirrored documents (what OpenSearch is here for)."""
        resp = await self._req("POST", "/_search", json={"size": size, "query": {"bool": {"must": [{"simple_query_string": {"query": query}}], "filter": [{"term": {"deleted": False}}]}}})
        return [h["_source"]["document"] for h in resp.json()["hits"]["hits"]]
