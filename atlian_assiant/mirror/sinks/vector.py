"""Vector mirror sinks (design 15): any ``MirrorStore`` plus a vector index fed through an ``Embedder`` (Chroma, pgvector, Weaviate, Pinecone adapters).

``VectorMirrorStore`` wraps a primary store: documents persist there; their text is embedded and indexed only when the version CHANGES, and a tombstone removes the vector,
so a deleted or permission-restricted page can never be retrieved later through similarity search.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ...core.errors import BackendUnavailableError
from ..base import MirrorStore


@runtime_checkable
class Embedder(Protocol):
    dimensions: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class VectorHit:
    id: str
    score: float
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class VectorIndex(Protocol):
    async def upsert(self, ident: str, vector: Sequence[float], metadata: Mapping[str, Any], text: str) -> None: ...
    async def delete(self, ident: str) -> None: ...
    async def query(self, vector: Sequence[float], k: int = 5, where: Mapping[str, Any] | None = None) -> list[VectorHit]: ...


class HashingEmbedder:
    """Deterministic, dependency-free bag-of-words embedding (feature hashing). For tests, demos and offline smoke runs; use a real model in production."""

    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dimensions
            for tok in re.findall(r"[a-z0-9]+", t.lower()):
                h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "big")
                v[h % self.dimensions] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


class CallableEmbedder:
    """Wrap any ``async fn(texts) -> vectors`` (a hosted embeddings API client)."""

    def __init__(self, fn: Callable[[Sequence[str]], Awaitable[list[list[float]]]], dimensions: int) -> None:
        self._fn, self.dimensions = fn, dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = await self._fn(texts)
        if any(len(v) != self.dimensions for v in vectors):
            raise ValueError(f"embedder returned a vector that is not {self.dimensions}-dimensional")
        return vectors


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class InMemoryVectorIndex:
    def __init__(self) -> None:
        self.items: dict[str, tuple[list[float], dict[str, Any], str]] = {}

    async def upsert(self, ident: str, vector: Sequence[float], metadata: Mapping[str, Any], text: str) -> None:
        self.items[ident] = (list(vector), dict(metadata), text)

    async def delete(self, ident: str) -> None:
        self.items.pop(ident, None)

    async def query(self, vector: Sequence[float], k: int = 5, where: Mapping[str, Any] | None = None) -> list[VectorHit]:
        hits = [VectorHit(i, cosine(vector, v), m) for i, (v, m, _) in self.items.items() if all(m.get(a) == b for a, b in (where or {}).items())]
        return sorted(hits, key=lambda h: (-h.score, h.id))[:k]


# ---------------------------------------------------------------------------------------------------------------------- adapters (each takes the client object)
class ChromaIndex:
    """``collection`` is a ``chromadb`` collection (``client.get_or_create_collection(...)``)."""

    def __init__(self, collection: Any) -> None:
        self.c = collection

    @classmethod
    def from_path(cls, path: str, name: str = "atlassian") -> ChromaIndex:
        try:
            import chromadb
        except ImportError as exc:
            raise BackendUnavailableError("chromadb is not installed", hint="pip install chromadb") from exc
        return cls(chromadb.PersistentClient(path=path).get_or_create_collection(name, metadata={"hnsw:space": "cosine"}))

    async def upsert(self, ident: str, vector: Sequence[float], metadata: Mapping[str, Any], text: str) -> None:
        self.c.upsert(ids=[ident], embeddings=[list(vector)], metadatas=[{k: v for k, v in metadata.items() if v is not None}], documents=[text])

    async def delete(self, ident: str) -> None:
        self.c.delete(ids=[ident])

    async def query(self, vector: Sequence[float], k: int = 5, where: Mapping[str, Any] | None = None) -> list[VectorHit]:
        r = self.c.query(query_embeddings=[list(vector)], n_results=k, where=dict(where) if where else None)
        return [VectorHit(i, 1.0 - float(d), m or {}) for i, d, m in zip(r["ids"][0], r["distances"][0], r["metadatas"][0], strict=True)]


class PgVectorIndex:
    """pgvector over a ``SqlExecutor`` (named parameters). The vector is bound as its text form and cast, so no driver-specific vector type is needed."""

    def __init__(self, executor: Any, *, table: str = "mirror_vectors", dimensions: int = 256) -> None:
        if not table.replace("_", "").isalnum():
            raise ValueError(f"unsafe table name {table!r}")
        self.x, self.t, self.dim, self._ready = executor, table, dimensions, False

    async def _init(self) -> None:
        if not self._ready:
            await self.x.execute("CREATE EXTENSION IF NOT EXISTS vector", {})
            await self.x.execute(f"CREATE TABLE IF NOT EXISTS {self.t} (id TEXT PRIMARY KEY, embedding vector({self.dim}), metadata JSONB, body TEXT)", {})
            self._ready = True

    @staticmethod
    def _vec(v: Sequence[float]) -> str:
        return "[" + ",".join(f"{x:.8g}" for x in v) + "]"

    async def upsert(self, ident: str, vector: Sequence[float], metadata: Mapping[str, Any], text: str) -> None:
        import json

        await self._init()
        await self.x.execute(f"INSERT INTO {self.t} (id, embedding, metadata, body) VALUES (:id, CAST(:v AS vector), CAST(:m AS JSONB), :b) "
                             f"ON CONFLICT (id) DO UPDATE SET embedding = CAST(:v AS vector), metadata = CAST(:m AS JSONB), body = :b",
                             {"id": ident, "v": self._vec(vector), "m": json.dumps(dict(metadata), default=str), "b": text})

    async def delete(self, ident: str) -> None:
        await self._init()
        await self.x.execute(f"DELETE FROM {self.t} WHERE id = :id", {"id": ident})

    async def query(self, vector: Sequence[float], k: int = 5, where: Mapping[str, Any] | None = None) -> list[VectorHit]:
        import json

        await self._init()
        rows = await self.x.fetch(f"SELECT id, metadata, 1 - (embedding <=> CAST(:v AS vector)) AS score FROM {self.t} WHERE metadata @> CAST(:w AS JSONB) ORDER BY embedding <=> CAST(:v AS vector) LIMIT :k",
                                  {"v": self._vec(vector), "w": json.dumps(dict(where or {})), "k": k})
        return [VectorHit(r["id"], float(r["score"]), r["metadata"] if isinstance(r["metadata"], dict) else json.loads(r["metadata"])) for r in rows]


class WeaviateIndex:
    """``collection`` is a ``weaviate`` v4 collection object (``client.collections.get(...)``)."""

    def __init__(self, collection: Any) -> None:
        self.c = collection

    @staticmethod
    def _uuid(ident: str) -> str:
        import uuid

        return str(uuid.uuid5(uuid.NAMESPACE_URL, ident))

    async def upsert(self, ident: str, vector: Sequence[float], metadata: Mapping[str, Any], text: str) -> None:
        props = {**{k: v for k, v in metadata.items() if v is not None}, "mirror_id": ident, "body": text}
        uid = self._uuid(ident)
        if self.c.data.exists(uid):
            self.c.data.replace(uuid=uid, properties=props, vector=list(vector))
        else:
            self.c.data.insert(uuid=uid, properties=props, vector=list(vector))

    async def delete(self, ident: str) -> None:
        self.c.data.delete_by_id(self._uuid(ident))

    async def query(self, vector: Sequence[float], k: int = 5, where: Mapping[str, Any] | None = None) -> list[VectorHit]:
        r = self.c.query.near_vector(near_vector=list(vector), limit=k, return_metadata=["distance"])
        return [VectorHit(o.properties["mirror_id"], 1.0 - float(o.metadata.distance), {a: b for a, b in o.properties.items() if a not in ("mirror_id", "body")}) for o in r.objects]


class PineconeIndex:
    """``index`` is a ``pinecone`` Index (``pc.Index(name)``); ``namespace`` keeps one mirror per namespace."""

    def __init__(self, index: Any, *, namespace: str = "atlassian") -> None:
        self.i, self.ns = index, namespace

    async def upsert(self, ident: str, vector: Sequence[float], metadata: Mapping[str, Any], text: str) -> None:
        self.i.upsert(vectors=[{"id": ident, "values": list(vector), "metadata": {**{k: v for k, v in metadata.items() if v is not None}, "text": text[:2000]}}], namespace=self.ns)

    async def delete(self, ident: str) -> None:
        self.i.delete(ids=[ident], namespace=self.ns)

    async def query(self, vector: Sequence[float], k: int = 5, where: Mapping[str, Any] | None = None) -> list[VectorHit]:
        r = self.i.query(vector=list(vector), top_k=k, include_metadata=True, namespace=self.ns, filter=dict(where) if where else None)
        return [VectorHit(m["id"], float(m["score"]), {a: b for a, b in m.get("metadata", {}).items() if a != "text"}) for m in r["matches"]]


def default_text(kind: str, entity_id: str, doc: Mapping[str, Any]) -> str:
    """What gets embedded: title / summary plus the Markdown body (restricted documents are skipped by the store, not here)."""
    return "\n\n".join(str(p) for p in (doc.get("title") or doc.get("summary"), doc.get("description_markdown") or doc.get("body_markdown")) if p)


class VectorMirrorStore:
    """``MirrorStore`` decorator: persist in ``inner``, embed + index on version change, un-index on tombstone."""

    def __init__(self, inner: MirrorStore, index: VectorIndex, embedder: Embedder, *, text_of: Callable[[str, str, Mapping[str, Any]], str] = default_text,
                 skip_restricted: bool = True) -> None:
        self.inner, self.index, self.embedder, self.text_of, self.skip_restricted = inner, index, embedder, text_of, skip_restricted
        self.embedded = 0

    async def get_version(self, kind: str, entity_id: str) -> str | None:
        return await self.inner.get_version(kind, entity_id)

    async def upsert(self, kind: str, entity_id: str, version: str, document: dict[str, Any]) -> None:
        unchanged = await self.inner.get_version(kind, entity_id) == version
        await self.inner.upsert(kind, entity_id, version, document)
        ident = f"{kind}:{entity_id}"
        restricted = self.skip_restricted and bool((document.get("restrictions") or {}).get("restricted") is not False and document.get("restrictions"))
        if restricted:
            await self.index.delete(ident)                          # restricted content never enters (or stays in) a similarity index shared across users
            return
        if unchanged:
            return
        text = self.text_of(kind, entity_id, document)
        if not text.strip():
            return
        (vector,) = await self.embedder.embed([text])
        self.embedded += 1
        await self.index.upsert(ident, vector, {"kind": kind, "id": entity_id, "scope": document.get("_scope"), "title": document.get("title") or document.get("summary"),
                                                "url": document.get("url")}, text)

    async def tombstone(self, kind: str, entity_id: str) -> None:
        await self.inner.tombstone(kind, entity_id)
        await self.index.delete(f"{kind}:{entity_id}")

    def known_ids(self, kind: str, scope: str) -> AsyncIterator[str]:
        return self.inner.known_ids(kind, scope)

    async def descendants(self, kind: str, entity_id: str) -> list[str]:
        return await self.inner.descendants(kind, entity_id)

    async def search(self, query: str, *, k: int = 5, where: Mapping[str, Any] | None = None) -> list[VectorHit]:
        (vector,) = await self.embedder.embed([query])
        return await self.index.query(vector, k, where)
