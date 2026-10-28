"""DM-40: Azure Function -> Service Bus relay (RelayProvisioner(cloud="azure"), AzureServiceBusSource, verify_dm_40)."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from atlian_assiant.events.catalog import Ingress, method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.pipeline import EventPipeline
from atlian_assiant.events.provision.relay import RelayProvisioner
from atlian_assiant.events.sources.relays import AzureServiceBusSource
from atlian_assiant.events.stores import MemoryDedupStore
from atlian_assiant.events.verify import EventInbox, verify_dm_40
from atlian_assiant.policy import CallbackConfirmation

from .relaysim import FakeServiceBusReceiver, relay_message
from .sitesim import SiteSim
from .test_relays import CASES, public

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)


def make(*bodies):
    sb = FakeServiceBusReceiver(bodies)
    return sb, AzureServiceBusSource(sb)


def test_dm_40_catalog_entry():
    m = method("DM-40")
    assert m.ingress is Ingress.MANAGED and m.consumer.endswith("AzureServiceBusSource") and "DM-40" in AzureServiceBusSource.method_ids


async def test_dm_40_provision_generates_the_iac_with_short_retention_and_deploys_after_confirmation(tmp_path):
    calls = []
    p = RelayProvisioner("azure", directory=tmp_path / "r", run=lambda argv, cwd: calls.append(argv) or CASES["azure"][2], preflight=public())
    out = await p.apply(await p.plan(), confirm=APPROVE)
    assert "maxDeliveryCount: 5" in (tmp_path / "r/main.bicep").read_text() and calls and out["callback_url"] == CASES["azure"][3]


async def test_dm_40_consumer_normalizes_settles_after_success_and_redelivers_after_failure():
    sb, src = make(relay_message("wh-1"), relay_message("wh-2"))
    first, second = await src.poll_once()
    assert (first.event_id, first.delivery.method_id) == ("wh-1", "DM-40")
    pipe = EventPipeline([src], dedup=MemoryDedupStore(), max_attempts=1, backoff_base=0)
    pipe.on()(lambda e: _noop())
    await pipe.process(first, src)
    await src.nack(second, error=RuntimeError("boom"))
    acked, nacked = (sb.completed, sb.abandoned)
    assert len(acked) == 1 and len(nacked) == 1


async def _noop():
    return None


async def test_dm_40_end_to_end_verify_through_the_relay():
    sb, src = make()
    inbox = EventInbox()

    async def on_issue(key):
        sb.queue.append(SimpleNamespace(body=relay_message(f"probe-{key}", key), message_id="probe", delivery_count=1))
        for ev in await src.poll_once():
            await inbox.push(ev)
    report = await verify_dm_40(client=SiteSim(on_issue=on_issue), config=EventPlaneConfig(probe_issue="SBX-40"), inbox=inbox, timeout=timedelta(seconds=2))
    assert report.ok and report.method_id == "DM-40"
