"""P6.MIRROR.001: MirrorStore protocol, JiraMirror, ConfluenceMirror (restrictions mirrored): initial load, incremental via the event plane, nightly reconcile."""
from datetime import UTC, datetime, timedelta

import pytest

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.capabilities import Product
from atlian_assiant.core.errors import PermissionDeniedError
from atlian_assiant.events.model import ChangeAction, ChangeEvent, EntityType, EventSourceKind
from atlian_assiant.mirror import MirrorClient, MirrorStore, MirrorVersionStore
from atlian_assiant.mirror.base import newer
from atlian_assiant.mirror.sinks.memory import InMemoryMirrorStore
from atlian_assiant.models.confluence import SearchHit
from atlian_assiant.policy import AccessPolicy
from atlian_assiant.testing import FakeBackend

pytestmark = pytest.mark.anyio


def make(**kw):
    fake = FakeBackend()
    client = ClientFactory().build_async(AtlassianConfig(site="example.atlassian.net"), backends=[], preset="service", policy=AccessPolicy(), extra_backends={BackendKind.REST: fake}, **kw)
    return client, fake, InMemoryMirrorStore()


def event(kind=EntityType.ISSUE, action=ChangeAction.UPDATED, key="PROJ-1", entity_id=None) -> ChangeEvent:
    return ChangeEvent(event_id="e1", source=EventSourceKind.JIRA_WEBHOOK, product=Product.JIRA, entity_type=kind, action=action, native_type="x", entity_key=key,
                       entity_id=entity_id, received_at=datetime.now(UTC))


def test_the_in_memory_store_satisfies_the_protocol_and_the_facade_builds_mirrors():
    client, _, store = make()
    assert isinstance(store, MirrorStore) and isinstance(client.mirror, MirrorClient)
    assert client.mirror.jira(store, jql="project = PROJ").scope == "default" and client.mirror.confluence(store, cql="space = ENG").cql == "space = ENG"


def test_version_comparison_handles_numbers_timestamps_and_the_absence_of_a_version():
    assert newer("2", "1") and not newer("1", "1") and not newer("9", "10") and newer("10", "9")
    assert newer("2026-09-02T10:00:00+00:00", "2026-09-01T10:00:00+00:00") and not newer("2026-09-01T10:00:00+00:00", "2026-09-02T10:00:00+00:00")
    assert newer("1", None) and newer("1", "tombstone")


# ------------------------------------------------------------------------------------------------------------------------------ Jira
async def test_initial_load_persists_identity_updated_url_and_rich_text_both_ways():
    client, _, store = make()
    res = await client.mirror.jira(store, jql="project = PROJ", scope="proj").initial_load()
    assert res.loaded == 3 and not res.errors
    doc = store.get("issue", "PROJ-1")
    assert doc["key"] == "PROJ-1" and doc["id"] == "10001" and doc["site"] == "example.atlassian.net" and doc["url"].endswith("/browse/PROJ-1") and doc["_scope"] == "proj"
    assert doc["updated"].startswith("2026-09-01") and doc["status"] == "In Progress" and doc["assignee"]["display_name"] == "Ann Lee" and doc["labels"] == ["backend"]
    assert doc["description_markdown"] == "Login fails with **500**" and doc["description_adf"]["type"] == "doc"            # Markdown AND the raw ADF
    assert await store.get_version("issue", "PROJ-1") == doc["updated"]


async def test_a_second_load_writes_nothing_because_every_version_is_current():
    client, _, store = make()
    m = client.mirror.jira(store, jql="project = PROJ")
    await m.initial_load()
    writes = store.writes
    assert (await m.initial_load()).loaded == 0 and store.writes == writes


async def test_incremental_events_refresh_from_fresh_state_and_respect_the_version_gate():
    client, fake, store = make()
    m = client.mirror.jira(store, jql="project = PROJ")
    await m.initial_load()
    fake.store.issues["PROJ-1"] = fake.store.issues["PROJ-1"].model_copy(update={"summary": "Login fails (fresh)", "updated": fake.store.issues["PROJ-1"].updated + timedelta(hours=1)})
    assert await m.apply_event(event()) == "upserted" and store.get("issue", "PROJ-1")["summary"] == "Login fails (fresh)"
    assert await m.apply_event(event()) == "stale"                                         # a duplicate / out-of-order delivery is harmless
    assert await m.apply_event(event(EntityType.COMMENT)) == "stale"                       # comment events refresh the issue
    assert await m.apply_event(event(EntityType.SPRINT)) == "ignored" and await m.apply_event(event(key=None)) == "ignored"


async def test_a_deleted_issue_is_tombstoned_whether_the_event_says_so_or_the_read_returns_404():
    client, fake, store = make()
    m = client.mirror.jira(store, jql="project = PROJ")
    await m.initial_load()
    assert await m.apply_event(event(action=ChangeAction.DELETED, key="PROJ-2")) == "tombstoned" and store.get("issue", "PROJ-2") is None
    del fake.store.issues["PROJ-3"]
    assert await m.apply_event(event(key="PROJ-3")) == "tombstoned" and await store.get_version("issue", "PROJ-3") is None


async def test_reconcile_diffs_id_sets_tombstones_the_vanished_and_adds_the_new():
    client, fake, store = make()
    m = client.mirror.jira(store, jql="project = PROJ", scope="proj")
    await m.initial_load()
    gone = fake.store.issues.pop("PROJ-3")
    fake.store.issues["PROJ-9"] = gone.model_copy(update={"key": "PROJ-9", "id": "10009"})
    fake.store.issues["PROJ-1"] = fake.store.issues["PROJ-1"].model_copy(update={"updated": fake.store.issues["PROJ-1"].updated + timedelta(days=1)})
    res = await m.reconcile()
    assert (res.deleted, res.created, res.updated, res.unchanged) == (1, 1, 1, 1) and res.compared == 4
    assert store.get("issue", "PROJ-3") is None and store.get("issue", "PROJ-9")["id"] == "10009"
    again = await m.reconcile()
    assert (again.deleted, again.created, again.updated, again.unchanged) == (0, 0, 0, 3)


async def test_detail_is_read_on_demand_and_not_stored():
    client, _, store = make()
    m = client.mirror.jira(store, jql="project = PROJ")
    await m.initial_load()
    assert [c.id for c in (await m.comments("PROJ-1")).items] == ["c1", "c2", "c3"] and "comments" not in store.get("issue", "PROJ-1")


async def test_the_mirror_is_an_event_plane_sink():
    client, fake, store = make()
    m = client.mirror.jira(store, jql="project = PROJ")
    await m.sink(event())
    assert store.get("issue", "PROJ-1")["key"] == "PROJ-1"


async def test_a_bad_document_is_reported_and_does_not_stop_the_load():
    client, _, store = make()
    original = store.upsert

    async def flaky(kind, entity_id, version, document):
        if entity_id == "PROJ-2":
            raise RuntimeError("disk full")
        await original(kind, entity_id, version, document)
    store.upsert = flaky                                                                  # type: ignore[method-assign]
    res = await client.mirror.jira(store, jql="project = PROJ").initial_load()
    assert res.loaded == 2 and res.errors == ["PROJ-2: RuntimeError: disk full"]


# ------------------------------------------------------------------------------------------------------------------------------ Confluence
def cql_hits(fake, *ids):
    fake.store.search = [SearchHit(content_id=i, type="page", title=f"T{i}") for i in ids]


async def test_confluence_initial_load_hydrates_in_batches_and_mirrors_restrictions_as_data():
    client, fake, store = make()
    cql_hits(fake, "123", "124", "125")
    m = client.mirror.confluence(store, cql="space = ENG", scope="eng", batch_size=2)
    res = await m.initial_load()
    assert res.loaded == 3
    doc = store.get("page", "123")
    assert doc["title"] == "Runbook" and doc["version"] == 3 and doc["body_markdown"] == "# Runbook\n\nRestart **nginx**" and doc["body_storage"].startswith("<h1>")
    assert doc["restrictions"]["restricted"] is True and doc["restrictions"]["read_groups"] == ["eng"] and doc["restrictions"]["read_users"] == ["acc-ann"]
    assert doc["_scope"] == "eng" and doc["_parent"] == "page:100" and doc["url"].endswith("/pages/123/Runbook")


async def test_unknown_restrictions_never_read_as_unrestricted():
    client, fake, store = make()
    cql_hits(fake, "123")

    async def denied(content_id):
        raise PermissionDeniedError("no", hint="x")
    fake.confluence.get_restrictions = denied                                                       # type: ignore[method-assign]
    await client.mirror.confluence(store, cql="space = ENG").initial_load()
    assert store.get("page", "123")["restrictions"] == {"restricted": None, "unknown": True}


async def test_confluence_events_refresh_and_a_delete_takes_known_children_with_it():
    client, fake, store = make()
    cql_hits(fake, "123", "124", "125")
    m = client.mirror.confluence(store, cql="space = ENG")
    await m.initial_load()
    for cid in ("124", "125"):
        store.docs[("page", cid)]["_parent"] = "page:123"
    page_event = lambda **kw: event(EntityType.PAGE, key=None, entity_id="123", **kw)       # noqa: E731
    assert await m.apply_event(page_event()) == "stale"
    fake.store.pages["123"] = fake.store.pages["123"].model_copy(update={"version": 4})
    assert await m.apply_event(page_event()) == "upserted" and store.get("page", "123")["version"] == 4
    assert await m.apply_event(event(EntityType.COMMENT, key=None, entity_id="123")) == "ignored"
    assert await m.apply_event(page_event(action=ChangeAction.DELETED)) == "tombstoned"
    assert [store.get("page", c) for c in ("123", "124", "125")] == [None, None, None]


async def test_confluence_reconcile_diffs_ids_and_runs_subtree_checks():
    client, fake, store = make()
    cql_hits(fake, "123", "124", "125")
    m = client.mirror.confluence(store, cql="space = ENG", scope="eng")
    await m.initial_load()
    store.docs[("page", "125")]["_parent"] = "page:124"
    cql_hits(fake, "123")                                                                # 124 vanished from the scope; 125 is its known child
    res = await m.reconcile()
    assert res.deleted == 2 and res.subtree_tombstoned == ["125"]
    assert store.get("page", "124") is None and store.get("page", "125") is None and store.get("page", "123") is not None


# ------------------------------------------------------------------------------------------------------------------------------ version store adapter
async def test_the_mirror_is_the_event_pipelines_version_store():
    store = InMemoryMirrorStore()
    await store.upsert("page", "1", "3", {"_parent": None})
    await store.upsert("page", "2", "1", {"_parent": "page:1"})
    versions = MirrorVersionStore(store)
    assert await versions.get("page:1") == "3" and await versions.children("page:1") == ["page:2"]
    assert await versions.invalidate("page:1") == ["page:1", "page:2"] and await versions.get("page:2") is None
