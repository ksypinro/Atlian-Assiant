"""DM-42: Cloudflare Worker -> Queue relay (RelayProvisioner(cloud="cloudflare"), CloudflareQueuePullSource, verify_dm_42)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from atlian_assiant.events.catalog import Ingress, method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.pipeline import EventPipeline
from atlian_assiant.events.provision.relay import RelayProvisioner
from atlian_assiant.events.sources.relays import CloudflareQueuePullSource
from atlian_assiant.events.stores import MemoryDedupStore
from atlian_assiant.events.verify import EventInbox, verify_dm_42
from atlian_assiant.policy import CallbackConfirmation

from .relaysim import cloudflare_http, envelope_message
from .sitesim import SiteSim
from .test_relays import CASES, public

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)


def make(*bodies):
    http, state = cloudflare_http(bodies)
    return state, CloudflareQueuePullSource(http, account_id="acc", queue_id="q", api_token="cf-token")


def test_dm_42_catalog_entry():
    m = method("DM-42")
    assert m.ingress is Ingress.MANAGED and m.consumer.endswith("CloudflareQueuePullSource") and "DM-42" in CloudflareQueuePullSource.method_ids


async def test_dm_42_provision_ships_the_worker_and_wrangler_config_with_secrets_set_separately(tmp_path):
    calls = []
    p = RelayProvisioner("cloudflare", directory=tmp_path / "r", run=lambda argv, cwd: calls.append(argv) or CASES["cloudflare"][2], preflight=public())
    out = await p.apply(await p.plan(), confirm=APPROVE)
    toml = (tmp_path / "r/wrangler.toml").read_text()
    assert "[[queues.producers]]" in toml and "message_retention_period = 3600" in toml and "wrangler secret put" in toml
    assert "export default" in (tmp_path / "r/index.js").read_text() and calls == [["wrangler", "deploy"]] and out["callback_url"].endswith(".workers.dev")


async def test_dm_42_consumer_normalizes_the_verified_envelope_and_acks_after_success_and_retries_after_failure():
    state, src = make(envelope_message("wh-1"), envelope_message("wh-2"))
    first, second = await src.poll_once()
    assert (first.event_id, first.entity_key, first.delivery.method_id) == ("wh-1", "PROJ-9", "DM-42")
    pipe = EventPipeline([src], dedup=MemoryDedupStore(), max_attempts=1, backoff_base=0)
    pipe.on()(lambda e: _noop())
    await pipe.process(first, src)
    await src.nack(second, error=RuntimeError("boom"))
    assert state["acks"] == ["lease0"] and state["retries"] == ["lease1"]


async def _noop():
    return None


async def test_dm_42_end_to_end_verify_through_the_worker_and_queue():
    state, src = make()
    inbox = EventInbox()

    async def on_issue(key):
        state["messages"].append({"id": "probe", "lease_id": "lp", "body": envelope_message(f"probe-{key}", key), "attempts": 1})
        for ev in await src.poll_once():
            await inbox.push(ev)
    report = await verify_dm_42(client=SiteSim(on_issue=on_issue), config=EventPlaneConfig(probe_issue="SBX-42"), inbox=inbox, timeout=timedelta(seconds=2))
    assert report.ok and report.method_id == "DM-42"
