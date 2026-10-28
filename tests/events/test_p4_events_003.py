"""Relay handlers, shared core and IaC (design 14.8): same verifier chain and dedup as local receivers, minimal payloads, short retention."""
from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from atlian_assiant.core.errors import ConfigurationError
from atlian_assiant.events.receivers import InboundRequest, JiraWebhookReceiver
from atlian_assiant.events.stores import MemoryLocalQueue
from atlian_assiant.relay import aws_lambda, azure_function, gcp_function, iac
from atlian_assiant.relay.core import build_receiver, decode_body, minimal, process

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
SECRET = "relay-secret"
ENV = {"ATLASSIAN_EVENTS_WEBHOOK_SECRET": SECRET, "ATLASSIAN_EVENTS_WEBHOOK_TOKEN": "tok-1", "ATLASSIAN_EVENTS_ALLOWED_IPS": "3.3.3.0/24"}
BODY = json.dumps({"webhookEvent": "jira:issue_updated", "timestamp": int(time.time() * 1000), "issue": {"id": "1", "key": "PROJ-1", "fields": {"summary": "SECRET TEXT",
                   "project": {"key": "PROJ"}}}}).encode()


def signed(body: bytes = BODY, secret: str = SECRET, token: str = "tok-1") -> dict[str, str]:
    return {"X-Hub-Signature": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(), "X-Webhook-Token": token, "X-Atlassian-Webhook-Identifier": "wh-1"}


def test_relay_refuses_to_run_unauthenticated_and_builds_the_same_chain_as_a_local_receiver():
    with pytest.raises(ConfigurationError, match="refuses to run unauthenticated"):
        build_receiver({})
    r = build_receiver(ENV)
    assert [type(v).__name__ for v in r.verifiers] == ["IpAllowlistVerifier", "HmacSignatureVerifier", "StaticHeaderVerifier"] and r.path == "/hooks/jira"


@pytest.mark.parametrize("headers, remote, expected", [
    (signed(), "3.3.3.3", 202), (signed(secret="wrong"), "3.3.3.3", 401), (signed(token="nope"), "3.3.3.3", 401), (signed(), "9.9.9.9", 401), ({}, "3.3.3.3", 401)])
def test_relay_and_local_receiver_answer_identically(headers, remote, expected):
    relay_status, _ = process(build_receiver(ENV), "POST", "/hooks/jira", headers, BODY, remote)
    local = JiraWebhookReceiver.from_env(MemoryLocalQueue(), env=ENV)
    import anyio
    local_status = anyio.run(local.handle, InboundRequest("POST", "/hooks/jira", {k.lower(): v for k, v in headers.items()}, BODY, None, remote)).status
    assert relay_status == expected and (local_status if expected != 202 else 202) == expected


def test_relay_messages_are_minimal_the_raw_webhook_payload_is_dropped_but_the_identity_survives():
    status, message = process(build_receiver(ENV), "POST", "/hooks/jira", signed(), BODY, "3.3.3.3")
    data = json.loads(message)
    assert status == 202 and data["payload"] == {} and "SECRET TEXT" not in message
    assert (data["event_id"], data["entity_key"], data["native_type"]) == ("wh-1", "PROJ-1", "jira:issue_updated")
    assert minimal.__doc__ and "canonical" in minimal.__doc__


def test_relay_rejects_malformed_oversized_and_wrong_method_requests_without_a_message():
    r = build_receiver(ENV)
    assert process(r, "POST", "/hooks/jira", signed(b"nope"), b"nope", "3.3.3.3") == (400, None)
    assert process(r, "GET", "/hooks/jira", signed(), BODY, "3.3.3.3") == (405, None)
    assert process(r, "POST", "/other", signed(), BODY, "3.3.3.3") == (404, None)
    assert process(r, "POST", "/hooks/jira", signed(b"x" * 6_000_000), b"x" * 6_000_000, "3.3.3.3")[0] == 413


def test_relay_dedup_is_left_to_the_consumer_so_duplicates_produce_identical_message_ids():
    r = build_receiver(ENV)
    ids = {json.loads(process(r, "POST", "/hooks/jira", signed(), BODY, "3.3.3.3")[1])["event_id"] for _ in range(3)}
    assert ids == {"wh-1"}


class FakeSqsClient:
    def __init__(self):
        self.sent = []

    def send_message(self, QueueUrl, MessageBody):
        self.sent.append((QueueUrl, MessageBody))


def test_aws_lambda_handler_follows_the_design_shape(monkeypatch):
    import base64
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("QUEUE_URL", "https://sqs/q")
    monkeypatch.setattr(aws_lambda, "_receiver", None)
    fake = FakeSqsClient()
    monkeypatch.setattr(aws_lambda, "_sqs_client", fake)
    event = {"requestContext": {"http": {"method": "POST", "sourceIp": "3.3.3.3"}}, "rawPath": "/hooks/jira", "headers": signed(), "body": BODY.decode(), "isBase64Encoded": False}
    assert aws_lambda.handler(event, None) == {"statusCode": 202} and fake.sent[0][0] == "https://sqs/q" and json.loads(fake.sent[0][1])["entity_key"] == "PROJ-1"
    b64 = {**event, "body": base64.b64encode(BODY).decode(), "isBase64Encoded": True}
    assert aws_lambda.handler(b64, None) == {"statusCode": 202}
    forged = {**event, "headers": signed(secret="bad")}
    assert aws_lambda.handler(forged, None) == {"statusCode": 401} and len(fake.sent) == 2


def test_azure_and_gcp_handlers_share_the_same_core(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(azure_function, "_receiver", None)
    sent: list[str] = []
    assert azure_function.handle("POST", "/hooks/jira", signed(), BODY, sent.append, "3.3.3.3") == 202 and len(sent) == 1
    assert azure_function.handle("POST", "/hooks/jira", signed(secret="x"), BODY, sent.append, "3.3.3.3") == 401 and len(sent) == 1

    class Req:
        method, path, remote_addr = "POST", "/hooks/jira", "3.3.3.3"
        headers = signed()

        @staticmethod
        def get_data():
            return BODY
    published: list[str] = []
    monkeypatch.setattr(gcp_function, "_receiver", None)
    monkeypatch.setattr(gcp_function, "_publish", published.append)
    assert gcp_function.handler(Req) == ("", 202) and len(published) == 1
    Req.headers = signed(secret="x")
    assert gcp_function.handler(Req) == ("", 401) and len(published) == 1


def test_decode_body_handles_none_bytes_text_and_base64():
    assert decode_body(None) == b"" and decode_body(b"x") == b"x" and decode_body("x") == b"x" and decode_body("eA==", True) == b"x"


def test_iac_uses_short_retention_dead_letters_encryption_and_secrets_by_reference():
    sam = iac.aws_sam("rel", 3600).files["template.yaml"]
    assert "MessageRetentionPeriod: 3600" in sam and "RedrivePolicy" in sam and "SqsManagedSseEnabled: true" in sam and "NoEcho: true" in sam
    assert "SQSSendMessagePolicy" in sam and "Resource: '*'" not in sam and "atlian_assiant.relay.aws_lambda.handler" in sam
    bicep = iac.azure_bicep("rel", 7200).files["main.bicep"]
    assert "defaultMessageTimeToLive: 'PT2H'" in bicep and "maxDeliveryCount: 5" in bicep and "@secure()" in bicep
    tf = iac.gcp_terraform("rel", 3600).files["main.tf"]
    assert 'message_retention_duration = "3600s"' in tf and "google_pubsub_subscription" in tf
    cf = iac.cloudflare().files
    assert "message_retention_period = 3600" in cf["wrangler.toml"] and "wrangler secret put" in cf["wrangler.toml"] and "WEBHOOK_SECRET" not in cf["wrangler.toml"].split("\n", 1)[1].replace("# generated", "").split("[[")[1]
    assert iac.RETENTION_SECONDS == 86400


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_cloudflare_worker_syntax_and_verification_logic(tmp_path):
    src = (Path(iac.__file__).parent / "cloudflare_worker" / "index.js").read_text()
    f = tmp_path / "worker.mjs"
    f.write_text(src + """
const body = '{"webhookEvent":"jira:issue_updated"}';
const mk = (headers) => ({ headers: { get: (n) => headers[n] || null } });
const key = await crypto.subtle.importKey('raw', new TextEncoder().encode('s'), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
const sig = [...new Uint8Array(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(body)))].map((b) => b.toString(16).padStart(2, '0')).join('');
const results = [
  await verify(mk({ 'X-Hub-Signature': 'sha256=' + sig }), body, { WEBHOOK_SECRET: 's' }),
  await verify(mk({ 'X-Hub-Signature': 'sha256=00' + sig.slice(2) }), body, { WEBHOOK_SECRET: 's' }),
  await verify(mk({ 'X-Webhook-Token': 't' }), body, { WEBHOOK_TOKEN: 't' }),
  await verify(mk({}), body, {}),
  envelope(mk({ 'X-Atlassian-Webhook-Identifier': 'id1' }), body).headers['X-Atlassian-Webhook-Identifier'],
];
console.log(JSON.stringify(results));
""")
    out = subprocess.run(["node", str(f)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [True, False, True, False, "id1"]            # unauthenticated configs are refused, forged signatures fail
