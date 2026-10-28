"""DM-38: webhook/trigger + periodic reconciliation (ReconcilerSource: id-set diff finds deletes, version diff finds missed updates)."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from atlian_assiant.capabilities import Product
from atlian_assiant.events.catalog import method
from atlian_assiant.events.model import ChangeAction, EntityType, EventSourceKind
from atlian_assiant.events.pipeline import EventPipeline
from atlian_assiant.events.sources import EventSource, Reconciler, ReconcilerSource
from atlian_assiant.events.stores import MemoryCheckpointStore, MemoryDedupStore
from atlian_assiant.events.verify import verify_dm_38
from atlian_assiant.testing.server import ScenarioServer

from ..conftest import make_rest_client

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


class World:
    def __init__(self, **entities: str) -> None:
        self.entities = dict(entities)

    async def snapshot(self):
        return dict(self.entities)


def make(world: World, cp=None, **kw) -> ReconcilerSource:
    return ReconcilerSource(world.snapshot, cp or MemoryCheckpointStore(), clock=lambda: T0, **kw)


def test_dm_38_catalog_entry():
    m = method("DM-38")
    assert m.implemented and m.provisioner is None and isinstance(make(World()), EventSource) and Reconciler is ReconcilerSource


async def test_dm_38_the_first_sweep_only_establishes_a_baseline():
    world = World(**{"issue:PROJ-1": "v1"})
    src = make(world)
    result = await src.sweep()
    assert (result.compared, result.created, result.changed, result.deleted) == (1, 0, 0, 0) and src._pending == []


async def test_dm_38_finds_created_changed_and_deleted_entities():
    world = World(**{"issue:PROJ-1": "v1", "issue:PROJ-2": "v1", "issue:PROJ-3": "v1"})
    src = make(world)
    await src.sweep()
    world.entities.update({"issue:PROJ-2": "v2", "issue:PROJ-4": "v1"})
    del world.entities["issue:PROJ-3"]
    result = await src.sweep()
    assert (result.compared, result.created, result.changed, result.deleted) == (3, 1, 1, 1)
    by_action = {e.action: e for e in src._pending}
    assert by_action[ChangeAction.CREATED].entity_key == "PROJ-4" and by_action[ChangeAction.UPDATED].entity_key == "PROJ-2" and by_action[ChangeAction.DELETED].entity_key == "PROJ-3"
    assert all(e.source is EventSourceKind.RECONCILER and e.delivery.method_id == "DM-38" and e.entity_type is EntityType.ISSUE for e in src._pending)
    assert by_action[ChangeAction.DELETED].event_id == "issue:PROJ-3:deleted" and by_action[ChangeAction.UPDATED].event_id == "issue:PROJ-2:v2"


async def test_dm_38_a_sweep_with_no_differences_emits_nothing_and_repeated_sweeps_are_stable():
    world = World(**{"issue:PROJ-1": "v1"})
    src = make(world)
    await src.sweep()
    for _ in range(3):
        result = await src.sweep()
        assert (result.created, result.changed, result.deleted) == (0, 0, 0)
    assert src._pending == []


async def test_dm_38_the_snapshot_survives_a_restart_through_the_checkpoint_store():
    cp = MemoryCheckpointStore()
    world = World(**{"issue:PROJ-1": "v1", "issue:PROJ-2": "v1"})
    await make(world, cp).sweep()
    del world.entities["issue:PROJ-2"]
    result = await make(world, cp).sweep()                     # a new instance, same store: the delete is still found
    assert result.deleted == 1


async def test_dm_38_confluence_pages_map_to_page_entities_and_delete_events_flow_through_the_pipeline():
    world = World(**{"page:100": "2026-09-01T10:00:00+00:00"})
    src = make(world, product=Product.CONFLUENCE)
    await src.sweep()
    world.entities.clear()
    await src.sweep()
    pipe = EventPipeline([src], dedup=MemoryDedupStore())
    seen = []

    @pipe.on(Product.CONFLUENCE, EntityType.PAGE, ChangeAction.DELETED)
    async def handle(ev):
        seen.append((ev.entity_id, ev.native_type))
    for ev in list(src._pending):
        await pipe.process(ev)
    assert seen == [("100", "reconcile:deleted")]


async def test_dm_38_events_loop_emits_sweeps_on_an_interval_until_closed():
    world = World(**{"issue:PROJ-1": "v1"})
    sleeps = []
    holder = {}

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        world.entities["issue:PROJ-1"] = f"v{len(sleeps) + 1}"
        if len(sleeps) == 2:
            await holder["src"].aclose()
    src = holder["src"] = make(world, interval=3600, sleep=fake_sleep)
    got = [e async for e in src.events()]
    assert sleeps == [3600, 3600] and [e.event_id for e in got] == ["issue:PROJ-1:v2"]     # closed during the 2nd sleep: no 3rd sweep
    await src.ack(got[0])
    await src.nack(got[0], error=RuntimeError("x"))


async def test_dm_38_for_jql_snapshots_every_page_through_the_client():
    server = ScenarioServer()
    client = make_rest_client(server)
    cp = MemoryCheckpointStore()
    src = ReconcilerSource.for_jql(client, "project = PROJ", cp, clock=lambda: T0)
    first = await src.sweep()
    assert first.compared >= 2 and all(k.startswith("issue:PROJ-") for k in json.loads(await cp.load(src.stream)))


async def test_dm_38_for_cql_snapshots_pages_through_the_client():
    server = ScenarioServer()
    cp = MemoryCheckpointStore()
    src = ReconcilerSource.for_cql(make_rest_client(server), "space = ENG", cp, clock=lambda: T0)
    assert (await src.sweep()).compared >= 1 and all(k.startswith("page:") for k in json.loads(await cp.load(src.stream)))


async def test_dm_38_verify_reports_what_one_sweep_found_and_fails_cleanly_on_errors():
    world = World(**{"issue:PROJ-1": "v1"})
    src = make(world)
    await src.sweep()
    world.entities["issue:PROJ-1"] = "v2"
    report = await verify_dm_38(reconciler=src)
    assert report.ok and report.method_id == "DM-38" and "1 changed" in report.detail

    async def boom():
        from atlian_assiant.core.errors import ServerError
        raise ServerError("503")
    bad = await verify_dm_38(reconciler=ReconcilerSource(boom, MemoryCheckpointStore()))
    assert not bad.ok and "sweep failed" in bad.detail
