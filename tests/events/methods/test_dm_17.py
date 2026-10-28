"""DM-17: Jira Automation -> Amazon SNS -> SQS (AwsQueueProvisioner + AutomationRuleProvisioner(SNS), SqsSource, verify_dm_17)."""
from __future__ import annotations

import json
from datetime import timedelta

import anyio
import pytest

from atlian_assiant.core.errors import InvalidRequestError
from atlian_assiant.events.automation import AutomationAction, AutomationRuleProvisioner, AutomationRuleTemplate
from atlian_assiant.events.catalog import Ingress, method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.model import ChangeAction, EntityType, EventSourceKind
from atlian_assiant.events.pipeline import EventPipeline
from atlian_assiant.events.provision.aws import AUTOMATION_PRINCIPAL_PLACEHOLDER, AwsQueueProvisioner, queue_policy
from atlian_assiant.events.provision.base import EventProvisioner, ProvisionMode
from atlian_assiant.events.sources import EventSource
from atlian_assiant.events.sources.sqs import SqsSource, unwrap_sns
from atlian_assiant.events.stores import MemoryDedupStore
from atlian_assiant.events.verify import EventInbox, verify_dm_17
from atlian_assiant.policy import CallbackConfirmation, DenyAll

from .awssim import aws
from .sitesim import SiteSim, envelope

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)
PRINCIPAL = "arn:aws:iam::111122223333:root"


def prov(clients=None, **kw) -> AwsQueueProvisioner:
    return AwsQueueProvisioner(clients, external_id=kw.pop("external_id", "ext-123"), automation_principal=kw.pop("automation_principal", PRINCIPAL), **kw)


def test_dm_17_catalog_entry():
    m = method("DM-17")
    assert m.ingress is Ingress.NONE and m.consumer.endswith("SqsSource") and isinstance(prov(), EventProvisioner)


async def test_dm_17_provision_creates_topic_queues_subscription_policy_and_role():
    clients = aws()
    p = prov(clients, kms_key_id="alias/events")
    plan = await p.plan()
    assert plan.method_id == "DM-17" and plan.outputs["mode"] == "api" and len(plan.steps) == 6 and not any("placeholder" in w for w in plan.warnings)
    out = await p.apply(plan, confirm=APPROVE)
    q = clients.sqs.queues[out["queue_url"]]
    redrive = json.loads(q["attrs"]["RedrivePolicy"])
    assert redrive["maxReceiveCount"] == "5" and redrive["deadLetterTargetArn"].endswith("atlassian-events-dlq") and q["attrs"]["KmsMasterKeyId"] == "alias/events"
    assert next(iter(clients.sns.subs.values()))["raw"] is True and out["topic_arn"].endswith(":atlassian-events") and out["role_arn"].endswith("atlassian-events-automation")


async def test_dm_17_provision_queue_policy_only_admits_the_topic_arn():
    clients = aws()
    p = prov(clients)
    out = await p.apply(await p.plan(), confirm=APPROVE)
    policy = json.loads(clients.sqs.queues[out["queue_url"]]["attrs"]["Policy"])
    cond = policy["Statement"][0]["Condition"]["ArnEquals"]["aws:SourceArn"]
    assert cond == out["topic_arn"] and policy["Statement"][0]["Principal"] == {"Service": "sns.amazonaws.com"}
    other = clients.sns.create_topic(Name="intruder")["TopicArn"]
    clients.sns.subscribe(other, "sqs", out["queue_arn"], {"RawMessageDelivery": "true"})
    assert clients.sns.publish(other, "evil") == 0 and clients.sqs.depth(out["queue_url"]) == 0           # another topic cannot write into the queue
    assert clients.sns.publish(out["topic_arn"], "good") == 1 and clients.sqs.depth(out["queue_url"]) == 1


async def test_dm_17_provision_role_trusts_automation_with_the_external_id_and_can_only_publish_to_the_topic():
    clients = aws()
    p = prov(clients)
    out = await p.apply(await p.plan(), confirm=APPROVE)
    trust = clients.iam.roles["atlassian-events-automation"]["Statement"][0]
    assert trust["Condition"] == {"StringEquals": {"sts:ExternalId": "ext-123"}} and trust["Principal"] == {"AWS": PRINCIPAL} and trust["Action"] == "sts:AssumeRole"
    policy = clients.iam.policies[("atlassian-events-automation", "publish-only")]["Statement"][0]
    assert policy == {"Effect": "Allow", "Action": "sns:Publish", "Resource": out["topic_arn"]}


async def test_dm_17_provision_needs_the_external_id_and_warns_about_the_unverified_principal():
    with pytest.raises(InvalidRequestError, match="external ID"):
        await AwsQueueProvisioner(aws(), external_id=None).plan()
    plan = await AwsQueueProvisioner(aws(), external_id="e").plan()
    assert any("placeholder" in w for w in plan.warnings) and any("VER-16" in w for w in plan.warnings) and AUTOMATION_PRINCIPAL_PLACEHOLDER in plan.warnings[0]


async def test_dm_17_provision_guided_without_boto3_clients_emits_the_policies_to_apply_by_hand():
    p = AwsQueueProvisioner(None, external_id="e", automation_principal=PRINCIPAL)
    plan = await p.plan()
    assert plan.outputs["mode"] == "guided"
    out = await p.apply(plan, confirm=APPROVE)
    assert "SNS topic" in out["instructions"] and "AllowSnsTopicOnly" in out["queue_policy"] and "sts:ExternalId" in out["trust_policy"]
    assert "AllowSnsTopicOnly" in queue_policy("q", "t")


async def test_dm_17_provision_declined_creates_nothing_and_teardown_removes_everything():
    clients = aws()
    p = prov(clients)
    with pytest.raises(Exception, match="declined"):
        await p.apply(await p.plan(), confirm=DenyAll())
    assert clients.sqs.queues == {} and clients.sns.topics == {}
    await p.apply(await p.plan(), confirm=APPROVE)
    await p.teardown(confirm=APPROVE)
    assert clients.sqs.queues == {} and clients.sns.topics == {} and clients.iam.roles == {}


async def test_dm_17_provision_the_automation_rule_template_for_sns_is_jira_only(tmp_path):
    t = AutomationRuleTemplate(name="to sns", product="jira", trigger="issue_created", action=AutomationAction.SNS, action_config={"topic_arn": "arn:aws:sns:x:1:t"})
    plan = await AutomationRuleProvisioner(t, output_dir=tmp_path).plan(mode=ProvisionMode.GUIDED)
    assert plan.method_id == "DM-17"


def sqs_setup():
    clients = aws()
    topic = clients.sns.create_topic(Name="t")["TopicArn"]
    url = clients.sqs.create_queue("q", {"RedrivePolicy": "{}"})["QueueUrl"]
    dlq = clients.sqs.create_queue("dlq")["QueueUrl"]
    arn = clients.sqs.queues[url]["attrs"]["QueueArn"]
    clients.sqs.set_queue_attributes(url, {"Policy": queue_policy(arn, topic), "RedrivePolicy": json.dumps({"deadLetterTargetArn": clients.sqs.queues[dlq]["attrs"]["QueueArn"],
                                                                                                         "maxReceiveCount": "2"})})
    return clients, topic, url, dlq, arn


async def test_dm_17_consumer_normalizes_the_automation_envelope_and_dedups_on_its_id():
    clients, topic, url, dlq, arn = sqs_setup()
    clients.sns.subscribe(topic, "sqs", arn, {"RawMessageDelivery": "true"})
    env = envelope("jira", "PROJ-1", "issue_created")
    clients.sns.publish(topic, json.dumps(env))
    clients.sns.publish(topic, json.dumps(env))                                       # Automation fired twice with the same envelope id
    src = SqsSource(clients.sqs, url, wait_seconds=0)
    assert isinstance(src, EventSource)
    events = await src.poll_once()
    assert len(events) == 2 and events[0].event_id == events[1].event_id == env["atl_event_id"]
    assert (events[0].entity_type, events[0].action, events[0].entity_key, events[0].source) == (EntityType.ISSUE, ChangeAction.CREATED, "PROJ-1", EventSourceKind.AWS_SQS)
    pipe, seen = EventPipeline([src], dedup=MemoryDedupStore()), []

    async def handle(ev):
        seen.append(ev.event_id)
    pipe.on()(handle)
    for ev in events:
        await pipe.process(ev, src)
    assert len(seen) == 1 and clients.sqs.depth(url) == 0                              # both deleted after processing; one handler run


async def test_dm_17_consumer_unwraps_the_sns_envelope_when_raw_delivery_is_off():
    clients, topic, url, dlq, arn = sqs_setup()
    clients.sns.subscribe(topic, "sqs", arn, {"RawMessageDelivery": "false"})
    clients.sns.publish(topic, json.dumps(envelope("jira", "PROJ-2", "field_changed")))
    ev = (await SqsSource(clients.sqs, url, wait_seconds=0).poll_once())[0]
    assert ev.entity_key == "PROJ-2" and unwrap_sns('{"Type":"Notification","Message":"x"}') == "x" and unwrap_sns("plain") == "plain"


async def test_dm_17_consumer_keeps_unparseable_messages_instead_of_dropping_them():
    clients, topic, url, dlq, arn = sqs_setup()
    clients.sqs.send_message(url, "not json at all")
    ev = (await SqsSource(clients.sqs, url, wait_seconds=0).poll_once())[0]
    assert ev.entity_type is EntityType.UNKNOWN and ev.event_id.startswith("m") and ev.native_type == "sqs:unparsed" and ev.payload["body"] == "not json at all"


async def test_dm_17_nack_leaves_the_message_then_redrive_moves_it_to_the_dlq_after_the_limit():
    clients, topic, url, dlq, arn = sqs_setup()
    clients.sqs.send_message(url, json.dumps(envelope("jira", "PROJ-3", "issue_created")))
    src = SqsSource(clients.sqs, url, wait_seconds=0, visibility_timeout=30)
    for attempt in (1, 2):
        ev = (await src.poll_once())[0]
        assert ev.delivery.attempt == attempt - 1
        await src.nack(ev, error=RuntimeError("boom"))
        clients.sqs.now += 31                                                         # visibility timeout passes: redelivered
    assert await src.poll_once() == [] and clients.sqs.depth(url) == 0 and clients.sqs.depth(dlq) == 1       # maxReceiveCount=2 reached -> DLQ


async def test_dm_17_heartbeat_extends_visibility_for_slow_handlers_and_stops_when_acked():
    clients, topic, url, dlq, arn = sqs_setup()
    clients.sqs.send_message(url, json.dumps(envelope("jira", "PROJ-4", "issue_created")))
    src = SqsSource(clients.sqs, url, wait_seconds=0, visibility_timeout=30)
    ev = (await src.poll_once())[0]
    stop = anyio.Event()
    async with anyio.create_task_group() as tg:
        tg.start_soon(lambda: src.heartbeat(stop, every=0.01))
        await anyio.sleep(0.08)
        assert clients.sqs.visibility_changes
        await src.ack(ev)
        before = len(clients.sqs.visibility_changes)
        await anyio.sleep(0.05)
        stop.set()
    assert len(clients.sqs.visibility_changes) == before and clients.sqs.depth(url) == 0


async def test_dm_17_events_loop_yields_messages_until_closed():
    clients, topic, url, dlq, arn = sqs_setup()
    clients.sqs.send_message(url, json.dumps(envelope("jira", "PROJ-5", "issue_created")))
    src = SqsSource(clients.sqs, url, wait_seconds=0, idle_sleep=0.001)
    got = []
    async for ev in src.events():
        got.append(ev)
        await src.aclose()
    assert len(got) == 1


async def test_dm_17_end_to_end_verify_through_sns_and_sqs():
    clients, topic, url, dlq, arn = sqs_setup()
    clients.sns.subscribe(topic, "sqs", arn, {"RawMessageDelivery": "true"})
    src = SqsSource(clients.sqs, url, wait_seconds=0)
    inbox = EventInbox()

    async def on_issue(key):
        clients.sns.publish(topic, json.dumps(envelope("jira", key, "field_changed")))
        for ev in await src.poll_once():
            await inbox.push(ev)
    report = await verify_dm_17(client=SiteSim(on_issue=on_issue), config=EventPlaneConfig(probe_issue="SBX-17"), inbox=inbox, timeout=timedelta(seconds=2))
    assert report.ok and report.method_id == "DM-17"
