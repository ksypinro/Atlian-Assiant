"""DM-41: Google Cloud Run / Function -> Pub/Sub relay (RelayProvisioner(cloud="gcp"), PubSubSource, verify_dm_41)."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from atlian_assiant.events.catalog import Ingress, method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.pipeline import EventPipeline
from atlian_assiant.events.provision.relay import RelayProvisioner
from atlian_assiant.events.sources.relays import PubSubSource
from atlian_assiant.events.stores import MemoryDedupStore
from atlian_assiant.events.verify import EventInbox, verify_dm_41
from atlian_assiant.policy import CallbackConfirmation

from .relaysim import FakePubSub, relay_message
from .sitesim import SiteSim
from .test_relays import CASES, public

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)


def make(*bodies):
    ps = FakePubSub(bodies)
    return ps, PubSubSource(ps, "projects/p/subscriptions/s")


def test_dm_41_catalog_entry():
    m = method("DM-41")
    assert m.ingress is Ingress.MANAGED and m.consumer.endswith("PubSubSource") and "DM-41" in PubSubSource.method_ids


async def test_dm_41_provision_generates_the_iac_with_short_retention_and_deploys_after_confirmation(tmp_path):
    calls = []
    p = RelayProvisioner("gcp", directory=tmp_path / "r", run=lambda argv, cwd: calls.append(argv) or CASES["gcp"][2], preflight=public())
    out = await p.apply(await p.plan(), confirm=APPROVE)
    assert "google_pubsub_subscription" in (tmp_path / "r/main.tf").read_text() and calls and out["callback_url"] == CASES["gcp"][3]


async def test_dm_41_consumer_normalizes_settles_after_success_and_redelivers_after_failure():
    ps, src = make(relay_message("wh-1"), relay_message("wh-2"))
    first, second = await src.poll_once()
    assert (first.event_id, first.delivery.method_id) == ("wh-1", "DM-41")
    pipe = EventPipeline([src], dedup=MemoryDedupStore(), max_attempts=1, backoff_base=0)
    pipe.on()(lambda e: _noop())
    await pipe.process(first, src)
    await src.nack(second, error=RuntimeError("boom"))
    acked, nacked = (ps.acked, [d[0][0] for d in ps.deadlines if d[1] == 0])
    assert len(acked) == 1 and len(nacked) == 1


async def _noop():
    return None


async def test_dm_41_end_to_end_verify_through_the_relay():
    ps, src = make()
    inbox = EventInbox()

    async def on_issue(key):
        ps.pending.append(SimpleNamespace(ack_id="probe", delivery_attempt=1, message=SimpleNamespace(data=relay_message(f"probe-{key}", key).encode(), message_id="probe")))
        for ev in await src.poll_once():
            await inbox.push(ev)
    report = await verify_dm_41(client=SiteSim(on_issue=on_issue), config=EventPlaneConfig(probe_issue="SBX-41"), inbox=inbox, timeout=timedelta(seconds=2))
    assert report.ok and report.method_id == "DM-41"
