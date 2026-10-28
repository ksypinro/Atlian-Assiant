"""Teamwork Graph / Rovo semantic search over an ``OpTransport`` (TWG CLI, GraphQL). Both consume Rovo credits (the policy gate is PolicyGuard)."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..core.http import RequestSpec
from ..core.urls import ApiSurface
from ..models.graph import GraphContext, GraphObject, SemanticHit
from .rest_cloud.base import RestBase

S = ApiSurface.JIRA_PLATFORM          # the surface is irrelevant to an OpTransport; it only labels the request


def graph_object(o: Mapping[str, Any]) -> GraphObject:
    return GraphObject(ari=str(o.get("ari", "")), type=o.get("type"), title=o.get("title"), url=o.get("url"), properties=o.get("properties", {}))


class GraphPart(RestBase):
    """Implements TeamworkGraphReader and SemanticSearcher."""

    async def _post(self, path: str, body: dict[str, Any], op: str) -> Any:
        return await self.transport.json(RequestSpec("POST", S, path, json=body, operation=op, idempotent=True))

    async def get_context(self, ari_or_url: str, *, max_items: int = 50) -> GraphContext:
        data = await self._post("/graph/context", {"ariOrUrl": ari_or_url, "maxItems": max_items}, "graph.context")
        return GraphContext(root=ari_or_url, related=[graph_object(o) for o in data.get("related", [])][:max_items], relationships=list(data.get("relationships", [])))

    async def get_objects(self, aris_or_urls: Sequence[str]) -> list[GraphObject]:
        data = await self._post("/graph/objects", {"ariOrUrls": list(aris_or_urls)}, "graph.object.get")
        return [graph_object(o) for o in (data if isinstance(data, list) else data.get("objects", []))]

    async def semantic_search(self, query: str, *, products: Sequence[str] = (), limit: int = 20) -> list[SemanticHit]:
        body: dict[str, Any] = {"query": query, "limit": limit}
        if products:
            body["products"] = list(products)
        data = await self._post("/graph/search", body, "graph.search.semantic")
        return [SemanticHit(title=h.get("title", ""), url=h.get("url"), product=h.get("product"), snippet=h.get("snippet"), score=h.get("score"))
                for h in (data if isinstance(data, list) else data.get("results", []))][:limit]
