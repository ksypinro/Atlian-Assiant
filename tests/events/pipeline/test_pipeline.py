"""EventPipeline + CanonicalEnricher + stores (design 14.12, 14.13): dedup, retry/backoff, DLQ, tombstones, cascade, ordering."""
from __future__ import annotations

from datetime import datetime

import anyio
import pytest

from atlian_assiant.capabilities import Product
from atlian_assiant.core.errors import NotFoundError, ServerError
from atlian_assiant.events.enrich import CanonicalEnricher, entity_ref
from atlian_assiant.events.model import ChangeAction as A
from atlian_assiant.events.model import EntityType as E
from atlian_assiant.events.pipeline import EventPipeline
from atlian_assiant.events.stores import (
    JsonlDeadLetterSink,
    MemoryCheckpointStore,
    MemoryDeadLetterSink,
    MemoryDedupStore,
    MemoryLocalQueue,
    MemoryVersionStore,
    SqliteCheckpointStore,
    SqliteDb,
    SqliteDeadLetterSink,
    SqliteDedupStore,
    SqliteLocalQueue,
)
from atlian_assiant.models.common import Tombstone
from atlian_assiant.models.confluence import Content
from atlian_assiant.models.jira import Issue

from ..conftest import ListSource, event

pytestmark = [pytest.mark.layer_11, pytest.mark.anyio]


def pipeline(events, **kw):
    source = ListSource(events)
    kw.setdefault("dedup", MemoryDedupStore())
    kw.setdefault("backoff_base", 0.0)
    return EventPipeline([source], **kw), source


def issue(version: str, key: str = "PROJ-1") -> Issue:
    return Issue(id="1", key=key, updated=datetime.fromisoformat(version))


def enricher(responses: dict, versions=None):
    calls: list[str] = []

    async def fetch_issue(key: str):
        calls.append(key)
        result = responses[key]
        if isinstance(result, BaseException):
            raise result
        return result

    async def fetch_content(content_id: str, kind: str):
        calls.append(content_id)
        result = responses[content_id]
        if isinstance(result, BaseException):
            raise result
        return result
    en = CanonicalEnricher(fetch_issue, fetch_content, versions or MemoryVersionStore())
    en.calls = calls
    return en


# ---------------------------------------------------------------- dedup and handlers
async def test_duplicate_deliveries_run_the_handler_once_and_are_acked():
    p, source = pipeline([event("a"), event("a"), event("a")])
    calls = []

    @p.on(Product.JIRA, E.ISSUE, A.UPDATED)
    async def handle(ev):
        calls.append(ev.event_id)

    await p.run()
    assert calls == ["a"] and p.stats.duplicates == 2 and source.acked == ["a", "a", "a"] and p.stats.handled == 1


async def test_handlers_are_selected_by_product_entity_and_action_and_none_matches_anything():
    p, _ = pipeline([event("1"), event("2", entity=E.COMMENT, native="comment_created", action=A.CREATED),
                     event("3", product=Product.CONFLUENCE, entity=E.PAGE, key=None, entity_id="9", action=A.UPDATED)])
    hits: dict[str, list[str]] = {"issue": [], "any_comment": [], "everything": []}
    p.on(Product.JIRA, E.ISSUE)(lambda ev: _add(hits["issue"], ev))
    p.on(None, E.COMMENT, None)(lambda ev: _add(hits["any_comment"], ev))
    p.on()(lambda ev: _add(hits["everything"], ev))
    await p.run()
    assert hits == {"issue": ["1"], "any_comment": ["2"], "everything": ["1", "2", "3"]}


async def _add(bucket, ev):
    bucket.append(ev.event_id)


async def test_filters_skip_events_but_still_ack_them():
    p, source = pipeline([event("a", key="PROJ-1"), event("b", key="OTHER-1")])
    p.filter(lambda ev: (ev.entity_key or "").startswith("PROJ"))
    seen = []
    p.on()(lambda ev: _add(seen, ev))
    await p.run()
    assert seen == ["a"] and p.stats.filtered == 1 and source.acked == ["a", "b"]


async def test_a_failing_handler_is_retried_with_backoff_then_succeeds():
    p, source = pipeline([event("a")], max_attempts=4)
    attempts = []

    @p.on()
    async def flaky(ev):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("temporary")

    await p.run()
    assert len(attempts) == 3 and p.stats.retried == 2 and p.stats.dead_lettered == 0 and source.acked == ["a"]


async def test_backoff_doubles_up_to_the_cap(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
    monkeypatch.setattr(anyio, "sleep", fake_sleep)
    p, _ = pipeline([event("a")], max_attempts=6, backoff_base=1.0, backoff_max=5.0)

    @p.on()
    async def always(ev):
        raise RuntimeError("x")

    await p.run()
    assert sleeps == [1.0, 2.0, 4.0, 5.0, 5.0]


async def test_exhausted_retries_go_to_the_dead_letter_sink_and_the_source_is_nacked():
    dlq = MemoryDeadLetterSink()
    p, source = pipeline([event("a"), event("b")], max_attempts=2, dead_letter=dlq)
    seen = []

    @p.on()
    async def handle(ev):
        if ev.event_id == "a":
            raise ValueError("poison")
        seen.append(ev.event_id)

    await p.run()
    assert seen == ["b"] and source.nacked == ["a"] and [e.event_id for e, _ in dlq.items] == ["a"] and "ValueError: poison" in dlq.items[0][1]
    assert "a" not in source.acked and not await p.dedup.seen("jira_webhook:a")     # not marked seen: it can be replayed


async def test_a_sink_outage_does_not_rerun_handlers_or_lose_the_event():
    class BadSink:
        async def publish(self, ev):
            raise OSError("kafka down")

    class GoodSink:
        def __init__(self):
            self.got = []

        async def publish(self, ev):
            self.got.append(ev.event_id)

    good = GoodSink()
    p, source = pipeline([event("a")], sinks=[BadSink(), good])
    runs = []
    p.on()(lambda ev: _add(runs, ev))
    await p.run()
    assert runs == ["a"] and p.stats.sink_errors == 1 and source.acked == ["a"] and good.got == ["a"]


async def test_run_stops_when_the_stop_event_is_set():
    class Endless(ListSource):
        async def events(self):
            i = 0
            while True:
                i += 1
                yield event(f"e{i}")
                await anyio.sleep(0.005)

    stop = anyio.Event()
    p = EventPipeline([Endless([])], dedup=MemoryDedupStore())
    n = []

    @p.on()
    async def handle(ev):
        n.append(1)
        if len(n) == 3:
            stop.set()

    with anyio.fail_after(2):
        await p.run(stop=stop)
    assert 3 <= len(n) < 50


async def test_concurrency_is_bounded():
    active = peak = 0
    p, _ = pipeline([event(f"e{i}") for i in range(12)], concurrency=3)

    @p.on()
    async def handle(ev):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await anyio.sleep(0.01)
        active -= 1

    await p.run()
    assert p.stats.handled == 12 and peak <= 3


# ---------------------------------------------------------------- enrichment
async def test_enricher_replaces_the_thin_payload_with_fresh_canonical_state():
    en = enricher({"PROJ-1": issue("2026-09-01T12:00:00+00:00")})
    p, _ = pipeline([event("a")], enricher=en)
    got = []
    p.on()(lambda ev: got.append(ev.canonical) or _noop())
    await p.run()
    assert isinstance(got[0], Issue) and got[0].key == "PROJ-1"


async def _noop():
    return None


async def test_a_404_becomes_a_tombstone_and_the_delete_event_is_handled():
    en = enricher({"PROJ-1": NotFoundError("gone")})
    p, _ = pipeline([event("a", action=A.UPDATED)], enricher=en)
    got = []
    p.on()(lambda ev: got.append((ev.action, ev.canonical)) or _noop())
    await p.run()
    assert got[0][0] is A.DELETED and isinstance(got[0][1], Tombstone) and got[0][1].entity_id == "PROJ-1" and en.stats.tombstones == 1


async def test_out_of_order_delivery_is_harmless_through_the_version_gate():
    versions = MemoryVersionStore()
    en = enricher({"PROJ-1": issue("2026-09-01T12:00:00+00:00")}, versions)
    p, _ = pipeline([event("new")], enricher=en)
    got = []
    p.on()(lambda ev: got.append(ev.event_id) or _noop())
    await p.run()
    en._fetch_issue = _const(issue("2026-09-01T11:00:00+00:00"))              # a late event: the fresh read is NOT newer than what we hold
    p2, source2 = pipeline([event("late")], enricher=en)
    p2.on()(lambda ev: got.append(ev.event_id) or _noop())
    await p2.run()
    assert got == ["new"] and p2.stats.stale == 1 and source2.acked == ["late"] and en.stats.stale == 1


def _const(value):
    async def fetch(key):
        return value
    return fetch


async def test_newer_versions_pass_and_equal_versions_are_dropped():
    en = enricher({"PROJ-1": issue("2026-09-01T12:00:00+00:00")})
    assert await en.enrich(event("a")) is not None
    assert await en.enrich(event("b")) is None                               # same version again
    en._fetch_issue = _const(issue("2026-09-01T12:00:01+00:00"))
    assert await en.enrich(event("c")) is not None


async def test_confluence_integer_versions_are_gated_numerically():
    en = enricher({"9": Content(id="9", version=10), "8": Content(id="8", version=9)})
    page = event("p", entity=E.PAGE, key=None, entity_id="9", product=Product.CONFLUENCE, native="avi:confluence:updated:page")
    assert await en.enrich(page) is not None
    en._fetch_content = _const2(Content(id="9", version=9))                  # 9 < 10 lexicographically AND numerically; 10 > 9 numerically only
    assert await en.enrich(page) is None
    en._fetch_content = _const2(Content(id="9", version=11))
    assert (await en.enrich(page)).entity_version == "11"


def _const2(value):
    async def fetch(content_id, kind):
        return value
    return fetch


async def test_deleting_a_parent_invalidates_known_descendants_and_schedules_a_subtree_check():
    versions = MemoryVersionStore()
    en = enricher({"1": Content(id="1", version=1), "2": Content(id="2", version=1, parent_id="1"), "3": Content(id="3", version=1, parent_id="2")}, versions)
    for cid in ("1", "2", "3"):
        await en.enrich(event(cid, entity=E.PAGE, key=None, entity_id=cid, product=Product.CONFLUENCE, native="avi:confluence:created:page"))
    assert await versions.children("page:1") == ["page:2"]
    en._fetch_content = _raise(NotFoundError("gone"))
    gone = await en.enrich(event("del", entity=E.PAGE, key=None, entity_id="1", product=Product.CONFLUENCE, action=A.DELETED, native="avi:confluence:deleted:page"))
    assert isinstance(gone.canonical, Tombstone) and "2 descendant" in gone.canonical.reason
    assert en.stats.subtree_checks == [("page:1", "page:2", "page:3")]
    assert await versions.get("page:2") is None and await versions.get("page:3") is None and await versions.get("page:1") == "tombstone"


def _raise(exc):
    async def fetch(*args):
        raise exc
    return fetch


async def test_bursts_for_one_entity_share_a_single_fetch():
    gate = anyio.Event()
    calls = []

    async def slow_issue(key):
        calls.append(key)
        await gate.wait()
        return issue("2026-09-01T12:00:00+00:00")
    en = CanonicalEnricher(slow_issue, _const2(None), MemoryVersionStore())
    results = []

    async def one(i):
        results.append(await en.enrich(event(f"e{i}")))

    async with anyio.create_task_group() as tg:
        for i in range(5):
            tg.start_soon(one, i)
        await anyio.sleep(0.02)
        gate.set()
    assert len(calls) == 1 and en.stats.coalesced == 4 and sum(r is not None for r in results) == 1     # the rest are version-gated duplicates


async def test_a_failed_fresh_read_is_not_lost_it_is_retried_via_the_dlq_path():
    en = enricher({"PROJ-1": ServerError("503")})
    dlq = MemoryDeadLetterSink()
    p, source = pipeline([event("a")], enricher=en, dead_letter=dlq)
    p.on()(lambda ev: _noop())
    await p.run()
    assert source.nacked == ["a"] and len(dlq.items) == 1 and p.stats.handled == 0


def test_entity_refs_are_stable_and_unresolvable_entities_have_none():
    assert entity_ref(event("a", key="PROJ-5")) == "issue:PROJ-5"
    assert entity_ref(event("a", entity=E.COMMENT, key="PROJ-5")) == "issue:PROJ-5"
    assert entity_ref(event("a", entity=E.PAGE, key=None, entity_id="9")) == "page:9"
    assert entity_ref(event("a", entity=E.SPRINT, key=None)) is None


# ---------------------------------------------------------------- stores
@pytest.fixture(params=["memory", "sqlite"])
def stores(request, tmp_path):
    if request.param == "memory":
        return MemoryCheckpointStore(), MemoryDedupStore(), MemoryLocalQueue(), MemoryDeadLetterSink()
    db = SqliteDb(f"sqlite:///{tmp_path}/events.db")
    return SqliteCheckpointStore(db), SqliteDedupStore(db), SqliteLocalQueue(db), SqliteDeadLetterSink(db)


async def test_checkpoint_store_round_trips_per_stream(stores):
    cp = stores[0]
    assert await cp.load("s") is None
    await cp.save("s", "1")
    await cp.save("s", "2")
    await cp.save("t", "x")
    assert await cp.load("s") == "2" and await cp.load("t") == "x"


async def test_dedup_store_marks_and_expires(stores):
    dedup = stores[1]
    assert not await dedup.seen("k")
    await dedup.mark("k", ttl=60)
    assert await dedup.seen("k")
    await dedup.mark("gone", ttl=-1)
    assert not await dedup.seen("gone")


async def test_local_queue_is_fifo_and_redelivers_only_unacked(stores):
    q = stores[2]
    assert await q.get() is None
    id1, id2 = await q.put(event("a")), await q.put(event("b"))
    first = await q.get()
    assert first is not None and first[1].event_id == "a" and await q.depth() == 2
    await q.ack(first[0])
    second = await q.get()
    assert second is not None and second[1].event_id == "b" and await q.depth() == 1
    assert id1 != id2


async def test_dead_letter_sinks_keep_the_event_and_the_reason(stores, tmp_path):
    dlq = stores[3]
    await dlq.put(event("a"), ValueError("bad"))
    jsonl = JsonlDeadLetterSink(tmp_path / "dlq" / "dead.jsonl")
    await jsonl.put(event("b"), RuntimeError("worse"))
    line = (tmp_path / "dlq" / "dead.jsonl").read_text().strip()
    assert '"RuntimeError: worse"' in line and '"event_id": "b"' in line


async def test_sqlite_queue_survives_a_restart_and_redelivers_inflight_items(tmp_path):
    url = f"sqlite:///{tmp_path}/q.db"
    q = SqliteLocalQueue(SqliteDb(url))
    await q.put(event("a"))
    got = await q.get()
    assert got is not None                                                    # taken but never acked: the process "crashes" here
    restarted = SqliteLocalQueue(SqliteDb(url))
    assert await restarted.requeue_inflight() == 1
    again = await restarted.get()
    assert again is not None and again[1].event_id == "a"


def test_sqlite_files_are_owner_only(tmp_path):
    SqliteDb(f"sqlite:///{tmp_path}/events.db")
    assert (tmp_path / "events.db").stat().st_mode & 0o077 == 0


async def test_version_store_invalidation_handles_cycles():
    v = MemoryVersionStore()
    await v.link("a", "b")
    await v.link("b", "a")
    await v.put("a", "1")
    assert sorted(await v.invalidate("a")) == ["a", "b"] and await v.get("a") is None


async def test_events_survive_a_pipeline_crash_midway_and_are_processed_after_restart():
    """At-least-once: an event is marked seen only after handlers succeed, so a crash re-delivers it and dedup keeps it single."""
    dedup = MemoryDedupStore()
    ran: list[str] = []
    p1, _ = pipeline([event("a")], dedup=dedup)

    @p1.on()
    async def crash(ev):
        raise RuntimeError("crash before completion")
    p1.max_attempts = 1
    await p1.run()
    assert not await dedup.seen("jira_webhook:a")
    p2, _ = pipeline([event("a")], dedup=dedup)
    p2.on()(lambda ev: _add(ran, ev))
    await p2.run()
    assert ran == ["a"] and await dedup.seen("jira_webhook:a")
