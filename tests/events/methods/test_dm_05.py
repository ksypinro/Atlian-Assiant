"""DM-05: failed-webhook replay GET /rest/api/3/webhook/failed (WebhookFailedReplaySource, verify_dm_05)."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from atlian_assiant.events.catalog import method
from atlian_assiant.events.model import ChangeAction, EntityType, EventSourceKind
from atlian_assiant.events.pipeline import EventPipeline
from atlian_assiant.events.sources import EventSource, WebhookFailedReplaySource
from atlian_assiant.events.stores import MemoryCheckpointStore, MemoryDedupStore
from atlian_assiant.events.verify import verify_dm_05
from atlian_assiant.models.webhooks import FailedWebhook
from atlian_assiant.testing.server import ScenarioServer

from ..conftest import make_client, make_rest_client
from .sitesim import SiteSim, jira_body

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def failure(fid: str, body: dict | str | None, when: datetime = T0) -> FailedWebhook:
    return FailedWebhook(id=fid, url="https://hooks.example.com/jira", body=json.dumps(body) if isinstance(body, dict) else body, failure_time=when)


def source(failures: list[FailedWebhook], cp=None) -> WebhookFailedReplaySource:
    calls = []

    async def fetch(after):
        calls.append(after)
        return [f for f in failures if after is None or f.failure_time > after]
    src = WebhookFailedReplaySource(fetch, cp or MemoryCheckpointStore(), clock=lambda: T0)
    src.calls = calls
    return src


def test_dm_05_catalog_entry_is_a_pure_consumer_with_no_ingress():
    m = method("DM-05")
    assert m.implemented and m.provisioner is None and m.ingress.value == "none" and isinstance(source([]), EventSource)


async def test_dm_05_replays_failed_bodies_as_events_keyed_by_failure_id():
    src = source([failure("f1", jira_body("PROJ-1")), failure("f2", jira_body("PROJ-2", "comment_created"))])
    events = await src.poll_once()
    assert [(e.event_id, e.entity_key, e.entity_type) for e in events] == [("failed:f1", "PROJ-1", EntityType.ISSUE), ("failed:f2", "PROJ-2", EntityType.COMMENT)]
    assert all(e.source is EventSourceKind.WEBHOOK_FAILED_REPLAY and e.delivery.method_id == "DM-05" and e.delivery.verified for e in events)


async def test_dm_05_a_failure_without_a_body_becomes_an_unknown_event_so_nothing_is_lost():
    events = await source([failure("f9", None), failure("f10", "not json")]).poll_once()
    assert [e.entity_type for e in events] == [EntityType.UNKNOWN] * 2 and events[0].action is ChangeAction.OTHER and events[0].payload["url"]


async def test_dm_05_the_checkpoint_advances_so_failures_are_not_replayed_twice():
    cp = MemoryCheckpointStore()
    failures = [failure("f1", jira_body("PROJ-1"), T0)]
    src = source(failures, cp)
    assert len(await src.poll_once()) == 1 and await cp.load("webhook-failed") == T0.isoformat()
    assert await src.poll_once() == [] and src.calls[1] == T0
    failures.append(failure("f2", jira_body("PROJ-2"), datetime(2026, 9, 1, 13, 0, tzinfo=UTC)))
    assert [e.event_id for e in await src.poll_once()] == ["failed:f2"]


async def test_dm_05_replayed_events_flow_through_the_pipeline_and_dedup_by_failure_id():
    src = source([failure("f1", jira_body("PROJ-1"))])
    pipe = EventPipeline([src], dedup=MemoryDedupStore())
    seen = []

    @pipe.on()
    async def handle(ev):
        seen.append(ev.event_id)
    for event in await src.poll_once() * 2:
        await pipe.process(event)
    assert seen == ["failed:f1"] and pipe.stats.duplicates == 1


async def test_dm_05_events_loop_polls_until_closed():
    import anyio
    src = source([failure("f1", jira_body("PROJ-1"))])
    got = []
    async for ev in src.events():
        got.append(ev.event_id)
        await src.aclose()
    assert got == ["failed:f1"]
    with anyio.fail_after(1):
        assert [e async for e in src.events()] == []


async def test_dm_05_reads_the_failed_api_through_the_client_with_the_checkpoint_as_after():
    server = ScenarioServer()
    server.failed_webhooks.append({"id": "f1", "url": "https://hooks.example.com/jira", "body": json.dumps(jira_body("PROJ-1")), "failureTime": 1788264000000})
    cp = MemoryCheckpointStore()
    src = WebhookFailedReplaySource.from_client(make_rest_client(server), cp)
    assert [e.entity_key for e in await src.poll_once()] == ["PROJ-1"]
    await src.poll_once()
    asked = [r for r in server.requests if r["path"].endswith("/webhook/failed")]
    assert "after" not in asked[0]["query"] and asked[1]["query"]["after"] == "1788264000000"


async def test_dm_05_verify_checks_the_failed_api_and_reports_pending_count():
    from atlian_assiant.events.verify import EventInbox
    ok = await verify_dm_05(client=make_client())
    assert ok.ok and ok.method_id == "DM-05" and "0 failed" in ok.detail
    site = SiteSim()
    site.failed_list = [1, 2]
    assert "2 failed" in (await verify_dm_05(client=site)).detail

    class Broken:
        class webhooks:  # noqa: N801
            @staticmethod
            async def failed():
                from atlian_assiant.core.errors import PermissionDeniedError
                raise PermissionDeniedError("nope")
    bad = await verify_dm_05(client=Broken(), inbox=EventInbox())
    assert not bad.ok and "unavailable" in bad.detail
