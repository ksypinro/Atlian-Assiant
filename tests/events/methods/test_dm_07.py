"""DM-07: Forge triggers -> Async Queue -> Storage inbox -> App REST pull (ForgeAppProvisioner(inbox), ForgeInboxSource, verify_dm_07)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from atlian_assiant.backends.forge import ForgeAppSpec, ForgeCliRunner
from atlian_assiant.capabilities import Product
from atlian_assiant.events.catalog import Ingress, method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.model import ChangeAction, EntityType, EventSourceKind
from atlian_assiant.events.pipeline import EventPipeline
from atlian_assiant.events.provision.forge import ForgeAppProvisioner
from atlian_assiant.events.sources import EventSource
from atlian_assiant.events.sources.forge import ForgeInboxSource
from atlian_assiant.events.stores import MemoryCheckpointStore, MemoryDedupStore
from atlian_assiant.events.verify import verify_dm_07
from atlian_assiant.policy import CallbackConfirmation

from ..conftest import make_client
from .forgesim import FakeForgeCli, FakeInboxApp
from .sitesim import SiteSim

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)


def source(app, cp=None, **kw) -> ForgeInboxSource:
    return ForgeInboxSource(app.events, app.ack, cp or MemoryCheckpointStore(), **kw)


def test_dm_07_catalog_entry():
    m = method("DM-07")
    assert m.preview and m.ingress is Ingress.NONE and m.consumer.endswith("ForgeInboxSource") and isinstance(source(FakeInboxApp()), EventSource)


async def test_dm_07_provision_generates_the_inbox_app_installs_both_products_and_flags_preview(tmp_path):
    cli = FakeForgeCli()
    prov = ForgeAppProvisioner(ForgeAppSpec(mode="inbox"), directory=tmp_path / "app", site="example.atlassian.net", cli=ForgeCliRunner(runner=cli, which=lambda b: "/f"))
    plan = await prov.plan()
    assert plan.method_id == "DM-07" and any("Preview" in w for w in plan.warnings) and any("preview_features_allowed is false" in w for w in plan.warnings)
    out = await prov.apply(plan, confirm=APPROVE)
    assert out["installed"] == "Confluence,Jira" and (tmp_path / "app/src/inbox.js").exists() and (tmp_path / "app/src/rest.js").exists()
    assert sum(c[1] == "install" for c in cli.calls) == 2
    ok = ForgeAppProvisioner(ForgeAppSpec(mode="inbox"), directory=tmp_path / "b", preview_features_allowed=True, cli=ForgeCliRunner(which=lambda b: None))
    assert not any("preview_features_allowed is false" in w for w in (await ok.plan()).warnings)


async def test_dm_07_consumer_pulls_normalizes_and_acks_only_after_handlers_succeed():
    app = FakeInboxApp()
    app.add(1)
    app.add(2, "avi:confluence:updated:page", {"id": "9001"})
    src = source(app)
    events = await src.poll_once()
    assert [(e.event_id, e.product, e.entity_type, e.action) for e in events] == [
        ("id1", Product.JIRA, EntityType.ISSUE, ChangeAction.UPDATED), ("id2", Product.CONFLUENCE, EntityType.PAGE, ChangeAction.UPDATED)]
    assert events[0].source is EventSourceKind.FORGE_INBOX and events[0].entity_key == "PROJ-1" and events[1].entity_id == "9001"
    assert app.acked == []                                                               # pulled, not yet processed
    pipe = EventPipeline([src], dedup=MemoryDedupStore(), max_attempts=1, backoff_base=0)
    handled = []

    @pipe.on(Product.JIRA)
    async def handle(ev):
        handled.append(ev.event_id)

    @pipe.on(Product.CONFLUENCE)
    async def explode(ev):
        raise RuntimeError("downstream down")
    for ev in events:
        await pipe.process(ev, src)
    assert handled == ["id1"] and app.acked == [app.records[0]["key"]]                    # the failed one stays in the inbox for the next pull


async def test_dm_07_consumer_checkpoints_the_cursor_pages_and_skips_processed_records():
    app = FakeInboxApp()
    for n in range(1, 6):
        app.add(n)
    app.records[0]["processed"] = True
    cp = MemoryCheckpointStore()
    src = source(app, cp, page_size=2)
    assert [e.event_id for e in await src.poll_once()] == ["id2", "id3", "id4", "id5"] and await cp.load("forge-inbox")


async def test_dm_07_deleted_pages_are_recorded_so_descendants_can_be_invalidated():
    app = FakeInboxApp()
    app.add(1, "avi:confluence:deleted:page", {"id": "77"})
    src = source(app)
    await src.poll_once()
    assert src.deleted_pages == ["77"]


async def test_dm_07_events_loop_polls_on_an_interval_until_closed():
    app = FakeInboxApp()
    app.add(1)
    sleeps, holder = [], {}

    async def sleep(seconds):
        sleeps.append(seconds)
        await holder["s"].aclose()
    src = holder["s"] = source(app, interval=42, sleep=sleep)
    got = [e async for e in src.events()]
    assert [e.event_id for e in got] == ["id1"] and sleeps == [42]
    await src.nack(got[0], error=RuntimeError("x"))
    assert app.acked == []


async def test_dm_07_from_backend_uses_the_app_rest_client():
    class Backend:
        class app_rest:  # noqa: N801
            calls = []

            @staticmethod
            async def events(**kw):
                Backend.app_rest.calls.append(kw)
                return {"events": [FakeInboxApp().add(1)], "nextCursor": None}

            @staticmethod
            async def ack(keys):
                Backend.app_rest.calls.append(keys)
    src = ForgeInboxSource.from_backend(Backend, MemoryCheckpointStore())
    ev = (await src.poll_once())[0]
    await src.ack(ev)
    assert Backend.app_rest.calls[0] == {"cursor": None, "limit": 100} and Backend.app_rest.calls[1][0].startswith("evt#")


async def test_dm_07_end_to_end_verify_sees_the_probe_arrive_through_the_inbox():
    app = FakeInboxApp()
    src = source(app)
    from atlian_assiant.events.verify import EventInbox
    inbox = EventInbox()

    async def on_issue(key):
        app.add(9, entity={"id": "1", "key": key})
        for ev in await src.poll_once():
            await inbox.push(ev)
    report = await verify_dm_07(client=SiteSim(on_issue=on_issue), config=EventPlaneConfig(probe_issue="SBX-5"), inbox=inbox, timeout=timedelta(seconds=2))
    assert report.ok and report.method_id == "DM-07"
    assert make_client() is not None
