"""P6.MIRROR.002: mirror sinks (SQLite, Postgres, OpenSearch, vector stores via the Embedder protocol) are adapters for MirrorStore: one conformance suite runs on all."""
import sys
import types

import pytest

from atlian_assiant.core.errors import BackendUnavailableError
from atlian_assiant.mirror import MirrorStore, MirrorVersionStore
from atlian_assiant.mirror.sinks import (
    CallableEmbedder,
    ChromaIndex,
    HashingEmbedder,
    InMemoryMirrorStore,
    InMemoryVectorIndex,
    OpenSearchMirrorStore,
    PgVectorIndex,
    PineconeIndex,
    SqlMirrorStore,
    VectorMirrorStore,
    WeaviateIndex,
    sqlite_store,
)
from atlian_assiant.mirror.sinks.sql import SqlAlchemyExecutor, SqliteExecutor
from atlian_assiant.testing.opensearchsim import FakeOpenSearch

pytestmark = pytest.mark.anyio


class SqliteAsPostgres(SqliteExecutor):
    """Runs the Postgres dialect of SqlMirrorStore on SQLite: JSONB becomes TEXT, everything else is shared SQL."""

    def _run(self, sql, params, commit):
        return super()._run(sql.replace("CAST(:doc AS JSONB)", ":doc").replace(" JSONB", " TEXT"), params, commit)


async def make(name: str):
    if name == "memory":
        return InMemoryMirrorStore()
    if name == "sqlite":
        return sqlite_store()
    if name == "postgres":
        return SqlMirrorStore(SqliteAsPostgres(), jsonb=True)
    if name == "opensearch":
        store = OpenSearchMirrorStore(FakeOpenSearch().client(), "http://os:9200")
        await store.ensure_index()
        return store
    if name == "vector":
        return VectorMirrorStore(sqlite_store(), InMemoryVectorIndex(), HashingEmbedder())
    raise KeyError(name)


@pytest.fixture(params=["memory", "sqlite", "postgres", "opensearch", "vector"])
async def store(request):
    return await make(request.param)


def doc(title="Runbook", scope="eng", parent=None, **extra):
    return {"_scope": scope, "_parent": parent, "id": "x", "title": title, "body_markdown": "Restart nginx", **extra}


async def ids(store, kind="page", scope="eng"):
    return [i async for i in store.known_ids(kind, scope)]


async def test_every_sink_is_a_mirror_store(store):
    assert isinstance(store, MirrorStore)


async def test_versions_roundtrip_and_unknown_is_none(store):
    assert await store.get_version("page", "1") is None
    await store.upsert("page", "1", "3", doc())
    assert await store.get_version("page", "1") == "3"
    await store.upsert("page", "1", "4", doc("Runbook v4"))
    assert await store.get_version("page", "1") == "4"


async def test_known_ids_are_scoped_sorted_and_exclude_tombstones(store):
    for i, scope in (("2", "eng"), ("1", "eng"), ("3", "ops")):
        await store.upsert("page", i, "1", doc(scope=scope))
    await store.upsert("issue", "PROJ-1", "1", doc(scope="eng"))
    assert await ids(store) == ["1", "2"] and await ids(store, scope="ops") == ["3"] and await ids(store, "issue") == ["PROJ-1"]
    await store.tombstone("page", "1")
    assert await ids(store) == ["2"] and await store.get_version("page", "1") is None
    await store.upsert("page", "1", "2", doc())                                    # a restored page comes back
    assert await ids(store) == ["1", "2"]


async def test_descendants_walk_the_parent_links_and_skip_tombstones(store):
    await store.upsert("page", "1", "1", doc())
    await store.upsert("page", "2", "1", doc(parent="page:1"))
    await store.upsert("page", "3", "1", doc(parent="page:2"))
    await store.upsert("page", "4", "1", doc(parent="page:1"))
    assert sorted(await store.descendants("page", "1")) == ["2", "3", "4"] and await store.descendants("page", "3") == []
    await store.tombstone("page", "4")
    assert sorted(await store.descendants("page", "1")) == ["2", "3"]


async def test_tombstoning_something_never_stored_is_harmless(store):
    await store.tombstone("page", "nope")
    assert await store.get_version("page", "nope") is None


async def test_the_version_store_adapter_works_on_every_sink(store):
    await store.upsert("page", "1", "3", doc())
    await store.upsert("page", "2", "1", doc(parent="page:1"))
    v = MirrorVersionStore(store)
    assert await v.get("page:1") == "3" and await v.children("page:1") == ["page:2"]
    assert await v.invalidate("page:1") == ["page:1", "page:2"] and await v.get("page:2") is None


# ------------------------------------------------------------------------------------------------------------------------------ sink specifics
async def test_sqlite_persists_to_a_file_and_the_document_roundtrips(tmp_path):
    path = str(tmp_path / "mirror.db")
    s1 = sqlite_store(path)
    await s1.upsert("page", "1", "3", doc(restrictions={"restricted": True, "read_groups": ["eng"]}))
    await s1.aclose()
    s2 = sqlite_store(path)
    assert await s2.get_version("page", "1") == "3" and (await s2.document("page", "1"))["restrictions"]["read_groups"] == ["eng"]


async def test_postgres_dialect_binds_the_document_as_jsonb_and_rejects_unsafe_table_names(monkeypatch):
    seen = []

    class Recorder(SqliteAsPostgres):
        async def execute(self, sql, params):
            seen.append(sql)
            await super().execute(sql, params)
    store = SqlMirrorStore(Recorder(), jsonb=True, table="mirror_docs")
    await store.upsert("page", "1", "1", doc())
    assert any("document JSONB" in s for s in seen) and any("CAST(:doc AS JSONB)" in s and "ON CONFLICT (kind, id) DO UPDATE" in s for s in seen)
    with pytest.raises(ValueError):
        SqlMirrorStore(SqliteExecutor(), table="x; DROP TABLE y")
    monkeypatch.setitem(sys.modules, "sqlalchemy", None)
    with pytest.raises(BackendUnavailableError) as exc:
        SqlAlchemyExecutor("postgresql://u@h/db")
    assert "atlian-assiant[db]" in exc.value.hint


async def test_opensearch_uses_plain_rest_and_is_searchable_as_text():
    fake = FakeOpenSearch()
    store = OpenSearchMirrorStore(fake.client(), "http://os:9200/", index="atl")
    await store.ensure_index()
    await store.ensure_index()                                                   # an existing index is fine
    await store.upsert("page", "1", "1", doc("Nginx runbook", body_markdown="restart the proxy"))
    await store.upsert("page", "2", "1", doc("Billing FAQ", body_markdown="refunds"))
    assert [d["title"] for d in await store.search_text("nginx")] == ["Nginx runbook"]
    await store.tombstone("page", "1")
    assert await store.search_text("nginx") == []
    assert all(p.startswith("/atl") for _, p in fake.requests)


async def test_opensearch_errors_are_typed():
    import httpx

    store = OpenSearchMirrorStore(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503, text="down"))), "http://os:9200")
    from atlian_assiant.core.errors import ServerError

    with pytest.raises(ServerError):
        await store.upsert("page", "1", "1", doc())


# ------------------------------------------------------------------------------------------------------------------------------ vectors
async def test_the_vector_store_embeds_on_version_change_only_and_searches():
    store = VectorMirrorStore(InMemoryMirrorStore(), InMemoryVectorIndex(), HashingEmbedder())
    await store.upsert("page", "1", "1", doc("Nginx runbook", body_markdown="restart nginx when the proxy hangs"))
    await store.upsert("page", "2", "1", doc("Billing FAQ", body_markdown="invoices and refunds"))
    await store.upsert("page", "1", "1", doc("Nginx runbook", body_markdown="restart nginx when the proxy hangs"))          # same version: no second embedding
    assert store.embedded == 2
    hits = await store.search("how do I restart the nginx proxy")
    assert [h.id for h in hits][0] == "page:1" and hits[0].metadata["title"] == "Nginx runbook" and hits[0].score > hits[1].score
    assert [h.id for h in await store.search("refunds", where={"scope": "eng"})][0] == "page:2"


async def test_a_tombstone_removes_the_vector_so_deleted_content_cannot_be_retrieved():
    store = VectorMirrorStore(InMemoryMirrorStore(), InMemoryVectorIndex(), HashingEmbedder())
    await store.upsert("page", "1", "1", doc("Secret plan", body_markdown="launch date"))
    await store.tombstone("page", "1")
    assert await store.search("launch date") == [] and await store.get_version("page", "1") is None


async def test_restricted_pages_never_enter_a_shared_similarity_index():
    store = VectorMirrorStore(InMemoryMirrorStore(), InMemoryVectorIndex(), HashingEmbedder())
    await store.upsert("page", "1", "1", doc("Open", body_markdown="public handbook", restrictions={"restricted": False}))
    await store.upsert("page", "2", "1", doc("HR", body_markdown="salary bands", restrictions={"restricted": True, "read_groups": ["hr"]}))
    await store.upsert("page", "3", "1", doc("Unknown", body_markdown="salary history", restrictions={"restricted": None, "unknown": True}))
    assert [h.id for h in await store.search("salary", k=5)] == ["page:1"]                  # neither the restricted nor the unknown-restriction page is retrievable
    await store.upsert("page", "1", "2", doc("Open", body_markdown="now restricted", restrictions={"restricted": True}))       # a page that BECOMES restricted leaves the index
    assert await store.search("handbook") == []
    assert await store.get_version("page", "2") == "1"                          # still mirrored in the primary store, just not searchable by similarity


async def test_the_callable_embedder_checks_dimensions():
    async def fn(texts):
        return [[1.0, 0.0] for _ in texts]
    assert await CallableEmbedder(fn, 2).embed(["a"]) == [[1.0, 0.0]]
    with pytest.raises(ValueError):
        await CallableEmbedder(fn, 3).embed(["a"])


# ------------------------------------------------------------------------------------------------------------------------------ adapters (stub clients)
async def test_the_chroma_adapter_maps_to_collection_calls():
    calls = []

    class Coll:
        def upsert(self, **kw): calls.append(("upsert", kw))
        def delete(self, **kw): calls.append(("delete", kw))
        def query(self, **kw):
            calls.append(("query", kw))
            return {"ids": [["page:1"]], "distances": [[0.25]], "metadatas": [[{"title": "T"}]]}
    idx = ChromaIndex(Coll())
    await idx.upsert("page:1", [0.1, 0.2], {"title": "T", "url": None}, "body")
    await idx.delete("page:1")
    hits = await idx.query([0.1, 0.2], 3, {"scope": "eng"})
    assert calls[0][1]["metadatas"] == [{"title": "T"}] and calls[1] == ("delete", {"ids": ["page:1"]}) and calls[2][1]["where"] == {"scope": "eng"}
    assert hits[0].id == "page:1" and hits[0].score == 0.75


async def test_the_pgvector_adapter_binds_vectors_as_text_and_creates_the_extension():
    sql = []

    class X:
        async def execute(self, s, p): sql.append((s, p))
        async def fetch(self, s, p):
            sql.append((s, p))
            return [{"id": "page:1", "metadata": '{"title": "T"}', "score": 0.9}]
    idx = PgVectorIndex(X(), dimensions=2)
    await idx.upsert("page:1", [0.5, 0.25], {"title": "T"}, "body")
    hits = await idx.query([0.5, 0.25], 2, {"scope": "eng"})
    assert sql[0][0] == "CREATE EXTENSION IF NOT EXISTS vector" and "vector(2)" in sql[1][0] and sql[2][1]["v"] == "[0.5,0.25]" and hits[0].metadata == {"title": "T"}
    with pytest.raises(ValueError):
        PgVectorIndex(X(), table="a; drop")


async def test_the_weaviate_and_pinecone_adapters_map_to_client_calls():
    log = []

    class Data:
        def __init__(self): self.have = set()
        def exists(self, u): return u in self.have
        def insert(self, **kw):
            self.have.add(kw["uuid"])
            log.append("insert")
        def replace(self, **kw): log.append("replace")
        def delete_by_id(self, u): log.append("delete")

    class Query:
        def near_vector(self, **kw):
            o = types.SimpleNamespace(properties={"mirror_id": "page:1", "title": "T", "body": "b"}, metadata=types.SimpleNamespace(distance=0.2))
            return types.SimpleNamespace(objects=[o])
    w = WeaviateIndex(types.SimpleNamespace(data=Data(), query=Query()))
    await w.upsert("page:1", [1.0], {"title": "T"}, "b")
    await w.upsert("page:1", [1.0], {"title": "T"}, "b")
    await w.delete("page:1")
    assert log == ["insert", "replace", "delete"] and (await w.query([1.0]))[0].score == 0.8

    sent = []

    class Index:
        def upsert(self, **kw): sent.append(("upsert", kw))
        def delete(self, **kw): sent.append(("delete", kw))
        def query(self, **kw): return {"matches": [{"id": "page:1", "score": 0.7, "metadata": {"title": "T", "text": "b"}}]}
    p = PineconeIndex(Index(), namespace="ns")
    await p.upsert("page:1", [1.0], {"title": "T"}, "b")
    assert sent[0][1]["namespace"] == "ns" and (await p.query([1.0]))[0].metadata == {"title": "T"}


def test_optional_clients_are_imported_lazily_with_an_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "chromadb", None)
    with pytest.raises(BackendUnavailableError) as exc:
        ChromaIndex.from_path("/tmp/x")
    assert "pip install chromadb" in exc.value.hint
