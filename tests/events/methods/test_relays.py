"""Shared contract for the relay methods DM-39..43: RelayProvisioner (IaC + deploy after confirmation) and the pull consumers."""
from __future__ import annotations

from datetime import timedelta

import pytest

from atlian_assiant.core.errors import CliExecutionError, InvalidRequestError, PolicyDeniedError
from atlian_assiant.events.catalog import Ingress, MethodKind, method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.model import ChangeAction, EntityType, EventSourceKind
from atlian_assiant.events.pipeline import EventPipeline
from atlian_assiant.events.provision.base import EventProvisioner, ProvisionMode
from atlian_assiant.events.provision.relay import RelayProvisioner
from atlian_assiant.events.receivers import WebhookUrlPreflight
from atlian_assiant.events.receivers.preflight import ProbeResult
from atlian_assiant.events.sources import EventSource
from atlian_assiant.events.sources.relays import (
    AzureServiceBusSource,
    CloudflareQueuePullSource,
    HttpPullBrokerSource,
    PubSubSource,
)
from atlian_assiant.events.stores import MemoryDedupStore
from atlian_assiant.events.verify import (
    EventInbox,
    verify_dm_39,
    verify_dm_40,
    verify_dm_41,
    verify_dm_42,
    verify_dm_43,
)
from atlian_assiant.policy import CallbackConfirmation, DenyAll

from .relaysim import FakePubSub, FakeServiceBusReceiver, broker_http, cloudflare_http, envelope_message, relay_message
from .sitesim import SiteSim

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)


def public() -> WebhookUrlPreflight:
    async def resolve(host, port):
        return ["8.8.8.8"]

    async def probe(url):
        return ProbeResult(True, status=401)
    return WebhookUrlPreflight(resolver=resolve, prober=probe)


CASES = {"aws": ("DM-39", "sam", "RelayUrl  https://abc.execute-api.eu-west-1.amazonaws.com/hooks/jira", "https://abc.execute-api.eu-west-1.amazonaws.com/hooks/jira"),
         "azure": ("DM-40", "az", "deployed: https://relay.azurewebsites.net/api/hook", "https://relay.azurewebsites.net/api/hook"),
         "gcp": ("DM-41", "terraform", "url = https://relay-abc.a.run.app", "https://relay-abc.a.run.app"),
         "cloudflare": ("DM-42", "wrangler", "Published https://atlassian-events-relay.me.workers.dev", "https://atlassian-events-relay.me.workers.dev")}


@pytest.mark.parametrize("dm", ["DM-39", "DM-40", "DM-41", "DM-42", "DM-43"])
def test_relay_catalog_entries(dm):
    m = method(dm)
    assert m.kind is MethodKind.TRANSPORT and m.ingress is Ingress.MANAGED and m.provisioner.endswith("RelayProvisioner") and dm in RelayProvisioner.method_ids


@pytest.mark.parametrize("cloud", list(CASES))
async def test_relay_provision_writes_iac_and_handler_deploys_after_confirmation_and_outputs_the_url(cloud, tmp_path):
    dm, binary, output, url = CASES[cloud]
    calls = []

    def run(argv, cwd):
        calls.append(argv)
        return output
    p = RelayProvisioner(cloud, directory=tmp_path / cloud, run=run, preflight=public())
    assert isinstance(p, EventProvisioner)
    plan = await p.plan()
    assert plan.method_id == dm and plan.outputs["mode"] == "api" and any("public URL" in w for w in plan.warnings) and any("short retention" in w for w in plan.warnings)
    assert calls == []                                           # planning deploys nothing
    out = await p.apply(plan, confirm=APPROVE)
    assert calls[0][0] == binary and out["callback_url"] == url and (tmp_path / cloud).exists() and int(out["files"]) >= 1
    if cloud in ("aws", "azure", "gcp"):
        assert (tmp_path / cloud / "core.py").exists()
    await p.teardown(confirm=APPROVE)
    assert not (tmp_path / cloud).exists()


@pytest.mark.parametrize("cloud", list(CASES))
async def test_relay_provision_declined_writes_and_deploys_nothing(cloud, tmp_path):
    ran = []
    p = RelayProvisioner(cloud, directory=tmp_path / "r", run=lambda a, c: ran.append(a) or "", preflight=public())
    with pytest.raises(Exception, match="declined"):
        await p.apply(await p.plan(), confirm=DenyAll())
    assert ran == [] and not (tmp_path / "r").exists()


async def test_relay_provision_without_a_runner_is_guided_and_cli_failures_surface(tmp_path):
    p = RelayProvisioner("aws", directory=tmp_path / "a", preflight=public())
    plan = await p.plan()
    assert plan.outputs["mode"] == "guided"
    out = await p.apply(plan, confirm=APPROVE)
    assert out["instructions"].startswith("cd ") and "sam deploy" in out["instructions"]
    with pytest.raises(PolicyDeniedError):
        await p.plan(mode=ProvisionMode.API)

    def failing(argv, cwd):
        raise CliExecutionError("sam failed: no credentials")
    bad = RelayProvisioner("aws", directory=tmp_path / "b", run=failing, preflight=public())
    with pytest.raises(CliExecutionError, match="no credentials"):
        await bad.apply(await bad.plan(), confirm=APPROVE)


async def test_relay_provision_flags_a_deployed_url_that_fails_the_callback_preflight(tmp_path):
    async def private(host, port):
        return ["10.0.0.5"]
    p = RelayProvisioner("cloudflare", directory=tmp_path, run=lambda a, c: "https://x.workers.dev", preflight=WebhookUrlPreflight(resolver=private))
    out = await p.apply(await p.plan(), confirm=APPROVE)
    assert "not publicly routable" in out["warnings"]


async def test_dm_43_broker_provisioning_registers_the_ingest_url_and_requires_https(tmp_path):
    p = RelayProvisioner("broker", directory=tmp_path, broker_ingest_url="https://broker.example.com/ingest/abc", preflight=public())
    plan = await p.plan()
    assert plan.method_id == "DM-43"
    out = await p.apply(plan, confirm=APPROVE)
    assert out["callback_url"] == "https://broker.example.com/ingest/abc" and p.relay_url == out["callback_url"]
    with pytest.raises(InvalidRequestError, match="https"):
        await RelayProvisioner("broker", directory=tmp_path, broker_ingest_url="http://x").plan()
    with pytest.raises(InvalidRequestError, match="unknown relay cloud"):
        RelayProvisioner("mars", directory=tmp_path)  # type: ignore[arg-type]


# ---------------------------------------------------------------- consumers
def sources():
    sb = FakeServiceBusReceiver([relay_message("wh-1")])
    ps = FakePubSub([relay_message("wh-1")])
    cf_http, cf_state = cloudflare_http([envelope_message("wh-1")])
    br_http, br_state = broker_http([relay_message("wh-1")])
    return {
        "DM-40": (AzureServiceBusSource(sb), lambda: (sb.completed, sb.abandoned)),
        "DM-41": (PubSubSource(ps, "projects/p/subscriptions/s"), lambda: (ps.acked, [d[0][0] for d in ps.deadlines if d[1] == 0])),
        "DM-42": (CloudflareQueuePullSource(cf_http, account_id="acc", queue_id="q", api_token="cf-token"), lambda: (cf_state["acks"], cf_state["retries"])),
        "DM-43": (HttpPullBrokerSource(br_http, "https://broker.example.com/pull", "https://broker.example.com/ack", auth={"Authorization": "Bearer brk"}),
                  lambda: (br_state["acked"], br_state["nacked"]))}


@pytest.mark.parametrize("dm", ["DM-40", "DM-41", "DM-42", "DM-43"])
async def test_relay_consumers_normalize_ack_after_success_and_nack_on_failure(dm):
    src, state = sources()[dm]
    assert isinstance(src, EventSource) and src.method_ids == {dm} and src.requires_public_ingress is False
    events = await src.poll_once()
    assert len(events) == 1 and events[0].event_id == "wh-1" and events[0].entity_key in ("PROJ-1", "PROJ-9") and events[0].delivery.transport_id
    assert events[0].entity_type is EntityType.ISSUE and events[0].action is ChangeAction.UPDATED
    acked, nacked = state()
    assert acked == [] and nacked == []                                   # nothing is settled before the handlers ran
    pipe = EventPipeline([src], dedup=MemoryDedupStore(), max_attempts=1, backoff_base=0)
    pipe.on()(lambda ev: _noop())
    await pipe.process(events[0], src)
    assert len(state()[0]) == 1 and state()[1] == []


async def _noop():
    return None


@pytest.mark.parametrize("dm", ["DM-40", "DM-41", "DM-42", "DM-43"])
async def test_relay_consumers_leave_failed_messages_for_redelivery(dm):
    src, state = sources()[dm]
    ev = (await src.poll_once())[0]
    await src.nack(ev, error=RuntimeError("handler failed"))
    acked, nacked = state()
    assert acked == [] and len(nacked) == 1
    await src.ack(ev)                                                       # already settled: a second ack is a no-op
    assert len(state()[0]) == 0


async def test_cloudflare_envelope_is_normalized_with_the_original_identifier_headers():
    src = sources()["DM-42"][0]
    ev = (await src.poll_once())[0]
    assert ev.event_id == "wh-1" and ev.source is EventSourceKind.JIRA_WEBHOOK and ev.delivery.attempt == 1 and ev.delivery.method_id == "DM-42"


async def test_cloudflare_requests_use_the_documented_pull_ack_calls_with_a_bearer_token():
    http, state = cloudflare_http([envelope_message("a"), envelope_message("b")])
    src = CloudflareQueuePullSource(http, account_id="acc", queue_id="q1", api_token="cf-token", batch_size=5)
    events = await src.poll_once()
    await src.ack(events[0])
    await src.nack(events[1], error=RuntimeError("x"))
    assert [(m, p.split("/")[-1], a) for m, p, a, _ in state["requests"]] == [("POST", "pull", "Bearer cf-token"), ("POST", "ack", "Bearer cf-token"), ("POST", "ack", "Bearer cf-token")]
    assert state["requests"][0][3] == {"batch_size": 5, "visibility_timeout_ms": 60000} and "/accounts/acc/queues/q1/messages" in state["requests"][0][1]


async def test_broker_uses_a_configured_auth_and_supports_callable_auth():
    http, state = broker_http([relay_message("wh-1")])

    async def auth():
        return {"Authorization": "Bearer dynamic"}
    src = HttpPullBrokerSource(http, "https://b/pull", "https://b/ack", auth=auth)
    ev = (await src.poll_once())[0]
    await src.ack(ev)
    assert set(state["auth"]) == {"Bearer dynamic"} and state["acked"] == ["b0"]


async def test_redelivered_messages_dedup_in_the_pipeline_by_the_webhook_identifier():
    sb = FakeServiceBusReceiver([relay_message("wh-1"), relay_message("wh-1")])
    src = AzureServiceBusSource(sb)
    events = await src.poll_once()
    pipe, seen = EventPipeline([src], dedup=MemoryDedupStore()), []

    async def handle(ev):
        seen.append(ev.event_id)
    pipe.on()(handle)
    for ev in events:
        await pipe.process(ev, src)
    assert seen == ["wh-1"] and len(sb.completed) == 2                      # both settled; the handler ran once


async def test_events_loops_poll_until_closed():
    sb = FakeServiceBusReceiver([relay_message("wh-1")])
    src = AzureServiceBusSource(sb, idle_sleep=0.001)
    got = []
    async for ev in src.events():
        got.append(ev)
        await src.aclose()
    assert len(got) == 1


@pytest.mark.parametrize("verify, dm", [(verify_dm_39, "DM-39"), (verify_dm_40, "DM-40"), (verify_dm_41, "DM-41"), (verify_dm_42, "DM-42"), (verify_dm_43, "DM-43")])
async def test_relay_end_to_end_verify(verify, dm):
    inbox = EventInbox()
    sb = FakeServiceBusReceiver([])

    async def on_issue(key):
        sb.queue.append(__import__("types").SimpleNamespace(body=relay_message(f"probe-{key}", key), message_id="probe", delivery_count=1))
        for ev in await AzureServiceBusSource(sb).poll_once():
            await inbox.push(ev)
    report = await verify(client=SiteSim(on_issue=on_issue), config=EventPlaneConfig(probe_issue="SBX-40"), inbox=inbox, timeout=timedelta(seconds=2))
    assert report.ok and report.method_id == dm
