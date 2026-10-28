"""P4 security controls (design section 17): SEC-12 Forge, SEC-15 relays and tunnels, SEC-17 AWS bridges, SEC-18 Forge Realtime."""
from __future__ import annotations

import json
import time

import jwt
import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric import rsa

from atlian_assiant.backends.forge import ForgeAppSpec, TriggerFilter, generate, required_scopes, select_events
from atlian_assiant.capabilities import Product
from atlian_assiant.core.errors import UnsupportedDeliveryPathError
from atlian_assiant.events.catalog import Ingress
from atlian_assiant.events.planner import Blueprint, EventPlaneFactory, EventRequirements
from atlian_assiant.events.provision.aws import (
    AwsQueueProvisioner,
    SsmProvisioner,
    automation_trust_policy,
    queue_policy,
    ssm_send_command_policy,
)
from atlian_assiant.events.receivers import InboundRequest
from atlian_assiant.events.receivers.forge import ForgeRemoteReceiver
from atlian_assiant.events.stores import MemoryLocalQueue
from atlian_assiant.policy import CallbackConfirmation
from atlian_assiant.relay import iac
from atlian_assiant.relay.core import build_receiver, process
from tests.events.methods.awssim import aws

pytestmark = [pytest.mark.layer_11, pytest.mark.anyio]


def test_sec_12_forge_apps_ignore_self_filter_triggers_use_minimal_scopes_and_have_no_egress_by_default():
    for mode in ("native", "inbox"):
        spec = ForgeAppSpec(mode=mode, products=("jira",), events=("issue:updated",))
        m = yaml.safe_load(generate(spec)["manifest.yml"])
        assert m["modules"]["trigger"][0]["filter"]["ignoreSelf"] is True and m["modules"]["trigger"][0]["events"] == ["avi:jira:updated:issue"]
        assert "external" not in m["permissions"]                                                    # no unnecessary egress
        assert set(m["permissions"]["scopes"]) <= {"read:jira-work", "storage:app"}                  # nothing broader than the events need
    narrow = ForgeAppSpec(products=("confluence",), events=("page:updated",))
    assert required_scopes(narrow, select_events(narrow)) == ["read:confluence-content.all", "storage:app"]
    assert TriggerFilter().ignore_self is True and TriggerFilter().on_error == "RECEIVE_AND_LOG"


async def test_sec_12_forge_remote_verifies_the_invocation_token_before_anything_else():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    q = MemoryLocalQueue()
    ep = ForgeRemoteReceiver(q, app_id="app-1", key_source=lambda _: key.public_key())
    body = json.dumps({"eventType": "avi:jira:updated:issue", "event": {"id": "1", "issue": {"id": "1", "key": "P-1"}}}).encode()
    good = jwt.encode({"aud": "app-1", "exp": time.time() + 60}, key, algorithm="RS256")
    wrong_aud = jwt.encode({"aud": "other", "exp": time.time() + 60}, key, algorithm="RS256")
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode({"aud": "app-1", "exp": time.time() + 60}, other_key, algorithm="RS256")
    statuses = [(await ep.handle(InboundRequest("POST", "/hooks/forge", {"Authorization": f"Bearer {t}"} if t else {}, body))).status for t in (None, wrong_aud, forged, good)]
    assert statuses == [401, 401, 401, 202] and await q.depth() == 1
    remote = ForgeAppSpec(mode="remote", remote_endpoint="https://py.example.com", products=("jira",))
    assert yaml.safe_load(generate(remote)["manifest.yml"])["permissions"]["external"]["fetch"]["backend"] == ["https://py.example.com"]       # egress limited to the one backend


def test_sec_15_relays_run_the_same_chain_minimal_payloads_short_retention_and_refuse_to_be_unauthenticated():
    import pytest as _pt

    from atlian_assiant.core.errors import ConfigurationError
    with _pt.raises(ConfigurationError):
        build_receiver({})
    env = {"ATLASSIAN_EVENTS_WEBHOOK_TOKEN": "t"}
    status, msg = process(build_receiver(env), "POST", "/hooks/jira", {"X-Webhook-Token": "t"}, b'{"webhookEvent":"jira:issue_updated","issue":{"key":"P-1","fields":{"summary":"private"}}}')
    assert status == 202 and "private" not in msg                                                       # minimal payload
    assert process(build_receiver(env), "POST", "/hooks/jira", {"X-Webhook-Token": "bad"}, b"{}") == (401, None)
    assert iac.RETENTION_SECONDS <= 86400 and "message_retention_period = 3600" in iac.cloudflare().files["wrangler.toml"]
    assert "NoEcho: true" in iac.aws_sam("x").files["template.yaml"]


def test_sec_15_tunnels_are_labelled_as_public_urls_and_never_chosen_for_strict_no_public_url():
    tunnel = EventPlaneFactory.blueprint(Blueprint.TUNNEL)
    assert any("public URL" in w and "strict" in w for w in tunnel.warnings)
    for max_ingress in (Ingress.NONE, Ingress.LAN_ONLY, Ingress.MANAGED):
        plan = EventPlaneFactory.recommend(EventRequirements(products=frozenset({Product.JIRA}), max_ingress=max_ingress, clouds=frozenset({"aws"})))
        assert "DM-44" not in plan.methods and Blueprint.TUNNEL not in plan.blueprints
    managed = EventPlaneFactory.blueprint(Blueprint.MANAGED_RELAY, cloud="aws")
    assert any("public URL" in w for w in managed.warnings)


async def test_sec_17_aws_bridges_bind_the_queue_to_the_topic_use_an_external_id_and_limit_ssm_to_one_document():
    clients = aws()
    prov = AwsQueueProvisioner(clients, external_id="ext-9", automation_principal="arn:aws:iam::1:root")
    out = await prov.apply(await prov.plan(), confirm=CallbackConfirmation(lambda p: True))
    policy = json.loads(queue_policy("q", out["topic_arn"]))["Statement"][0]
    assert policy["Condition"] == {"ArnEquals": {"aws:SourceArn": out["topic_arn"]}} and policy["Principal"] == {"Service": "sns.amazonaws.com"}
    assert clients.sns.publish(clients.sns.create_topic(Name="evil")["TopicArn"], "x") == 0
    trust = json.loads(automation_trust_policy("arn:aws:iam::1:root", "ext-9"))["Statement"][0]
    assert trust["Condition"] == {"StringEquals": {"sts:ExternalId": "ext-9"}}
    assert clients.iam.policies[("atlassian-events-automation", "publish-only")]["Statement"][0]["Resource"] == out["topic_arn"]
    ssm = json.loads(ssm_send_command_policy("eu-west-1", "123456789012", "OnlyThisDoc", "k", "v"))["Statement"]
    assert ssm[0]["Resource"].endswith("document/OnlyThisDoc") and all(s["Action"] == "ssm:SendCommand" for s in ssm) and all("Condition" in s for s in ssm[1:])
    sp = SsmProvisioner(clients, handler="a:b", external_id=None)
    assert (await sp.plan()).warnings and "*" not in ssm[0]["Resource"]


def test_sec_18_realtime_channels_are_scoped_by_default_and_global_channels_need_an_explicit_design():
    files = generate(ForgeAppSpec(realtime_panel="issue"))
    assert "changes-${contextId}" in files["src/panel.js"] and "global" not in files["src/panel.js"].lower().replace("no global channel", "")
    with pytest.raises(UnsupportedDeliveryPathError) as exc:
        generate(ForgeAppSpec(realtime_panel="issue", realtime_global=True))
    assert "authorization design" in exc.value.hint and "EV section 16" in exc.value.hint
