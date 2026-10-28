"""Mirror sinks: adapters for ``MirrorStore`` (design 15)."""
from __future__ import annotations

from .memory import InMemoryMirrorStore
from .opensearch import OpenSearchMirrorStore
from .sql import SqlMirrorStore, postgres_store, sqlite_store
from .vector import (
                     CallableEmbedder,
                     ChromaIndex,
                     Embedder,
                     HashingEmbedder,
                     InMemoryVectorIndex,
                     PgVectorIndex,
                     PineconeIndex,
                     VectorHit,
                     VectorIndex,
                     VectorMirrorStore,
                     WeaviateIndex,
)

__all__ = ["CallableEmbedder", "ChromaIndex", "Embedder", "HashingEmbedder", "InMemoryMirrorStore", "InMemoryVectorIndex", "OpenSearchMirrorStore", "PgVectorIndex", "PineconeIndex",
           "SqlMirrorStore", "VectorHit", "VectorIndex", "VectorMirrorStore", "WeaviateIndex", "postgres_store", "sqlite_store"]
