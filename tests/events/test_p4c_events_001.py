"""Downstream sinks beyond DM-49's core: webhook forwarding (LAN only) and re-notification of people (design 14.12)."""
from __future__ import annotations

import json

import httpx
import pytest

from atlian_assiant.core.errors import InvalidRequestError
from atlian_assiant.events.sinks import (
    EmailSink,
    JsmOpsAlertSink,
    SlackSink,
    TeamsSink,
    TwilioSmsSink,
    WebhookForwardSink,
    event_json,
)
from atlian_assiant.events.sinks.notify import summary
from atlian_assiant.models.jira import Issue

from .conftest import event

pytestmark = [pytest.mark.layer_11, pytest.mark.anyio]


def http(status=200):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), dict(request.headers), request.content, request.url.params))
        return httpx.Response(status, json={})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


def test_event_json_drops_the_raw_payload_and_includes_enriched_canonical_state():
    ev = event("a").model_copy(update={"payload": {"x": "RAW"}, "canonical": Issue(id="1", key="PROJ-1", summary="Real summary")})
    data = json.loads(event_json(ev))
    assert "payload" not in data and data["canonical"]["summary"] == "Real summary" and "raw" not in data["canonical"]
    assert "payload" in json.loads(event_json(ev, include_payload=True))


async def test_webhook_forward_sink_is_lan_only_unless_explicitly_allowed():
    for ok in ("http://10.0.0.5/hook", "https://svc.internal/hook", "http://localhost:9000/x", "http://ingest/hook", "https://192.168.1.2/x", "https://wiki.corp/hook"):
        WebhookForwardSink(None, ok)
    for bad in ("https://api.example.com/hook", "http://8.8.8.8/x"):
        with pytest.raises(InvalidRequestError, match="internal"):
            WebhookForwardSink(None, bad)
    WebhookForwardSink(None, "https://api.example.com/hook", allow_public=True)
    client, calls = http()
    await WebhookForwardSink(client, "http://10.0.0.5/hook", headers={"X-Token": "t"}).publish(event("a"))
    assert json.loads(calls[0][2])["event_id"] == "a" and calls[0][1]["x-token"] == "t"
    failing, _ = http(500)
    with pytest.raises(httpx.HTTPStatusError):
        await WebhookForwardSink(failing, "http://10.0.0.5/hook").publish(event("a"))


async def test_people_sinks_send_only_key_title_and_link_never_bodies():
    ev = event("a").model_copy(update={"canonical": Issue(id="1", key="PROJ-1", summary="Login fails"), "payload": {"description": "PRIVATE BODY"}})
    text = summary(ev, "example.atlassian.net")
    assert text == "[jira:issue_updated] PROJ-1 Login fails https://example.atlassian.net/browse/PROJ-1" and "PRIVATE" not in text

    class Slack:
        def __init__(self):
            self.posts = []

        async def chat_postMessage(self, channel, text):
            self.posts.append((channel, text))
    slack = Slack()
    await SlackSink(slack, "C1", site="example.atlassian.net").publish(ev)
    assert slack.posts == [("C1", text)]
    client, calls = http()
    await TeamsSink(client, "https://example.webhook.office.com/x", site="example.atlassian.net").publish(ev)
    assert json.loads(calls[0][2]) == {"text": text}
    await TwilioSmsSink(client, "AC1", "tok", "+1555", "+1666", site="example.atlassian.net").publish(ev)
    assert calls[1][1]["authorization"].startswith("Basic ") and "PRIVATE" not in calls[1][2].decode() and "To=%2B1666" in calls[1][2].decode()


async def test_email_sink_supports_sync_and_async_senders():
    sent = []

    async def send_async(msg):
        sent.append(("async", msg["Subject"]))
    await EmailSink(lambda m: sent.append(("sync", m["Subject"])), "bot@corp.example", ["ann@corp.example", "bob@corp.example"]).publish(event("a"))
    await EmailSink(send_async, "bot@corp.example", ["ann@corp.example"]).publish(event("b"))
    assert [k for k, _ in sent] == ["sync", "async"] and "PROJ-1" in sent[0][1]


async def test_jsm_ops_alert_sink_uses_the_dedup_key_as_alias_so_repeats_collapse():
    class Ops:
        def __init__(self):
            self.created = []

        async def create_alert(self, message, alias=None, priority="P3"):
            self.created.append((message, alias, priority))
    ops = Ops()
    await JsmOpsAlertSink(ops, priority="P2").publish(event("a"))
    await JsmOpsAlertSink(ops, priority="P2").publish(event("a"))
    assert [(a, p) for _, a, p in ops.created] == [("jira_webhook:a", "P2")] * 2
