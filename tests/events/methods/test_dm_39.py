"""DM-39: AWS API Gateway + Lambda -> SQS relay (RelayProvisioner(aws), SqsSource, verify_dm_39)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from atlian_assiant.events.catalog import Ingress, method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.pipeline import EventPipeline
from atlian_assiant.events.provision.relay import RelayProvisioner
from atlian_assiant.events.sources import SqsSource
from atlian_assiant.events.stores import MemoryDedupStore
from atlian_assiant.events.verify import EventInbox, verify_dm_39
from atlian_assiant.policy import CallbackConfirmation

from .awssim import aws
from .relaysim import relay_message
from .sitesim import SiteSim
from .test_relays import CASES, public

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)


def queue_with(*bodies):
    clients = aws()
    url = clients.sqs.create_queue("relay")["QueueUrl"]
    for b in bodies:
        clients.sqs.send_message(url, b)
    return clients, url, SqsSource(clients.sqs, url, wait_seconds=0, method_id="DM-39")


def test_dm_39_catalog_entry():
    m = method("DM-39")
    assert m.ingress is Ingress.MANAGED and m.consumer.endswith("SqsSource") and "DM-39" in SqsSource.method_ids


async def test_dm_39_provision_generates_a_sam_template_with_dlq_and_the_lambda_handler_then_deploys(tmp_path):
    calls = []
    p = RelayProvisioner("aws", directory=tmp_path / "r", run=lambda argv, cwd: calls.append(argv) or CASES["aws"][2], preflight=public(), name="my-relay")
    out = await p.apply(await p.plan(), confirm=APPROVE)
    template = (tmp_path / "r/template.yaml").read_text()
    assert "RedrivePolicy" in template and "atlian_assiant.relay.aws_lambda.handler" in template and (tmp_path / "r/aws_lambda.py").exists()
    assert calls[0][:4] == ["sam", "deploy", "--no-confirm-changeset", "--stack-name"] and "my-relay" in calls[0] and out["callback_url"].endswith("/hooks/jira")


async def test_dm_39_consumer_unwraps_the_relay_event_keeps_the_webhook_identifier_and_deletes_after_success():
    clients, url, src = queue_with(relay_message("wh-1"))
    ev = (await src.poll_once())[0]
    assert ev.event_id == "wh-1" and ev.delivery.method_id == "DM-39" and ev.entity_key == "PROJ-1"
    pipe = EventPipeline([src], dedup=MemoryDedupStore())
    pipe.on()(lambda e: _noop())
    await pipe.process(ev, src)
    assert clients.sqs.depth(url) == 0


async def _noop():
    return None


async def test_dm_39_failed_messages_stay_in_the_queue_for_redelivery():
    clients, url, src = queue_with(relay_message("wh-2"))
    ev = (await src.poll_once())[0]
    await src.nack(ev, error=RuntimeError("boom"))
    assert clients.sqs.depth(url) == 1


async def test_dm_39_end_to_end_verify_through_the_relay_queue():
    clients, url, src = queue_with()
    inbox = EventInbox()

    async def on_issue(key):
        clients.sqs.send_message(url, relay_message(f"probe-{key}", key))
        for ev in await src.poll_once():
            await inbox.push(ev)
    report = await verify_dm_39(client=SiteSim(on_issue=on_issue), config=EventPlaneConfig(probe_issue="SBX-39"), inbox=inbox, timeout=timedelta(seconds=2))
    assert report.ok and report.method_id == "DM-39"
