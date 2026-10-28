"""DM-43: dedicated webhook relay / broker service (RelayProvisioner(cloud="broker"), HttpPullBrokerSource, verify_dm_43)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from atlian_assiant.events.catalog import Ingress, method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.pipeline import EventPipeline
from atlian_assiant.events.provision.relay import RelayProvisioner
from atlian_assiant.events.sources.relays import HttpPullBrokerSource
from atlian_assiant.events.stores import MemoryDedupStore
from atlian_assiant.events.verify import EventInbox, verify_dm_43
from atlian_assiant.policy import CallbackConfirmation

from .relaysim import broker_http, relay_message
from .sitesim import SiteSim
from .test_relays import public

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)


def make(*bodies):
    http, state = broker_http(bodies)
    return state, HttpPullBrokerSource(http, "https://broker.example.com/pull", "https://broker.example.com/ack", auth={"Authorization": "Bearer brk"})


def test_dm_43_catalog_entry():
    m = method("DM-43")
    assert m.ingress is Ingress.MANAGED and m.consumer.endswith("HttpPullBrokerSource") and "DM-43" in HttpPullBrokerSource.method_ids


async def test_dm_43_provision_registers_the_brokers_ingest_url_as_the_callback_and_deploys_nothing(tmp_path):
    ran = []
    p = RelayProvisioner("broker", directory=tmp_path / "r", run=lambda a, c: ran.append(a) or "", broker_ingest_url="https://broker.example.com/ingest/abc", preflight=public())
    plan = await p.plan()
    assert plan.method_id == "DM-43" and "pull + ack" in plan.steps[0].description
    out = await p.apply(plan, confirm=APPROVE)
    assert out["callback_url"] == "https://broker.example.com/ingest/abc" and ran == [] and not (tmp_path / "r").exists()


async def test_dm_43_consumer_acks_after_success_and_nacks_after_failure_with_the_configured_auth():
    state, src = make(relay_message("wh-1"), relay_message("wh-2"))
    first, second = await src.poll_once()
    pipe = EventPipeline([src], dedup=MemoryDedupStore(), max_attempts=1, backoff_base=0)
    pipe.on()(lambda e: _noop())
    await pipe.process(first, src)
    await src.nack(second, error=RuntimeError("boom"))
    assert state["acked"] == ["b0"] and state["nacked"] == ["b1"] and set(state["auth"]) == {"Bearer brk"}


async def _noop():
    return None


async def test_dm_43_end_to_end_verify_through_the_broker():
    state, src = make()
    inbox = EventInbox()

    async def on_issue(key):
        state["messages"].append({"id": "probe", "body": relay_message(f"probe-{key}", key)})
        for ev in await src.poll_once():
            await inbox.push(ev)
    report = await verify_dm_43(client=SiteSim(on_issue=on_issue), config=EventPlaneConfig(probe_issue="SBX-43"), inbox=inbox, timeout=timedelta(seconds=2))
    assert report.ok and report.method_id == "DM-43"
