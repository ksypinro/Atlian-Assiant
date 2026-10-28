"""P3 security controls (design section 17): SEC-10 webhook ingress, SEC-11 private pull, SEC-13 Automation rules, SEC-19 probes and PoCs."""
from __future__ import annotations

import hashlib
import hmac
import json
import stat
import time
from datetime import UTC, datetime, timedelta

import pytest

from atlian_assiant.capabilities import Access
from atlian_assiant.core.errors import NotFoundError
from atlian_assiant.events.pipeline import EventPipeline
from atlian_assiant.events.receivers import (
    AllOf,
    HmacSignatureVerifier,
    InboundRequest,
    IpAllowlistVerifier,
    JiraWebhookReceiver,
    ReplayWindowVerifier,
    WebhookUrlPreflight,
)
from atlian_assiant.events.sources import JqlPollingSource
from atlian_assiant.events.stores import (
    MemoryCheckpointStore,
    MemoryDedupStore,
    MemoryLocalQueue,
    SqliteDb,
    SqliteDeadLetterSink,
    SqliteLocalQueue,
)
from atlian_assiant.events.verify import EndToEndProbe, EventInbox, issue_property_trigger
from tests.events.conftest import ListSource, event, make_client

pytestmark = [pytest.mark.layer_11, pytest.mark.anyio]


def _signed(body: bytes, key: bytes = b"k", ident: str = "i") -> dict[str, str]:
    return {"X-Hub-Signature": "sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest(), "X-Atlassian-Webhook-Identifier": ident}


async def test_sec_10_webhook_ingress_requires_https_a_verifier_chain_an_ip_allowlist_and_a_replay_window():
    now = time.time()
    chain = AllOf(IpAllowlistVerifier(["3.3.3.0/24"]), ReplayWindowVerifier(60), HmacSignatureVerifier("k"))
    ep = JiraWebhookReceiver(MemoryLocalQueue(), verifiers=[chain])
    body = json.dumps({"webhookEvent": "jira:issue_updated", "timestamp": int(now * 1000)}).encode()
    assert (await ep.handle(InboundRequest("POST", "/hooks/jira", _signed(body), body, None, "3.3.3.3"))).status == 202
    assert (await ep.handle(InboundRequest("POST", "/hooks/jira", _signed(body, ident="b"), body, None, "6.6.6.6"))).status == 401          # wrong source IP
    old = json.dumps({"timestamp": int((now - 7200) * 1000)}).encode()
    assert (await ep.handle(InboundRequest("POST", "/hooks/jira", _signed(old, ident="c"), old, None, "3.3.3.3"))).status == 401           # replayed
    assert (await ep.handle(InboundRequest("POST", "/hooks/jira", _signed(body, b"bad", "d"), body, None, "3.3.3.3"))).status == 401        # forged

    async def public(host, port):
        return ["8.8.8.8"]
    assert not (await WebhookUrlPreflight(resolver=public, network=False).check("http://hooks.example.com/x")).ok


async def test_sec_10_webhook_ingress_acknowledges_fast_by_queueing_and_rate_limits_when_overloaded():
    class CountingQueue(MemoryLocalQueue):
        puts = 0

        async def put(self, event):
            CountingQueue.puts += 1
            return await super().put(event)
    handled = []
    ep = JiraWebhookReceiver(CountingQueue(), max_queue_depth=2)
    for i in range(4):
        r = await ep.handle(InboundRequest("POST", "/hooks/jira", {"X-Atlassian-Webhook-Identifier": f"id{i}"}, b'{"webhookEvent":"jira:issue_updated"}'))
        handled.append(r.status)
    assert handled == [202, 202, 429, 429] and CountingQueue.puts == 2          # work happens later, off the request path; excess is shed with 429


async def test_sec_10_a_forged_request_never_reaches_the_queue_and_leaks_no_detail():
    queue = MemoryLocalQueue()
    ep = JiraWebhookReceiver(queue, verifiers=[HmacSignatureVerifier("k")])
    resp = await ep.handle(InboundRequest("POST", "/hooks/jira", {"X-Hub-Signature": "sha256=deadbeef"}, b"{}"))
    assert resp.status == 401 and resp.body == b"" and await queue.depth() == 0


async def test_sec_11_private_pull_is_outbound_only_with_a_dead_letter_sink_and_lag_monitoring(tmp_path):
    db = SqliteDb(f"sqlite:///{tmp_path}/events.db")
    assert stat.S_IMODE((tmp_path / "events.db").stat().st_mode) == 0o600                  # queued payloads are owner-readable only
    dlq = SqliteDeadLetterSink(db)
    pipe = EventPipeline([ListSource([event("a")])], dedup=MemoryDedupStore(), dead_letter=dlq, max_attempts=1)

    @pipe.on()
    async def handle(ev):
        raise RuntimeError("poison")
    await pipe.run()
    assert (await db.run("SELECT count(*) FROM dead_letters"))[0][0] == 1
    queue = SqliteLocalQueue(db)
    await queue.put(event("q"))
    assert await queue.depth() == 1                                                       # queue depth is observable for lag alerts
    cp, now = MemoryCheckpointStore(), {"t": datetime(2026, 9, 1, tzinfo=UTC)}

    async def search(jql, *, limit, cursor):
        from atlian_assiant.models.common import Page
        return Page(items=[])
    poller = JqlPollingSource(search, "project = P", cp, clock=lambda: now["t"])
    await poller.poll_once()
    now["t"] += timedelta(minutes=30)
    assert await poller.lag() == timedelta(minutes=30)                                    # a stalled poller shows up as growing lag


def test_sec_13_describe_warns_that_automation_rules_sending_fields_to_external_domains_must_be_locked_down():
    manifest = make_client().describe()
    warning = next(n for n in manifest["notes"] if "Automation rules" in n)
    assert "external domains" in warning and "locked down" in warning and "standard envelope" in warning
    assert "DM-15" in manifest["events"]["implemented_methods"] and manifest["events"]["planned_methods"] == 49 - len(manifest["events"]["implemented_methods"])


async def test_sec_19_probes_mutate_only_the_configured_sandbox_target_and_need_write_plus_confirmation():
    # 1. no configured target -> nothing is touched
    client = make_client()
    report = await EndToEndProbe("DM-03", target=None, target_name="probe_issue", trigger=issue_property_trigger(client), inbox=EventInbox()).run()
    assert not report.ok and client.seen == []
    # 2. the trigger only ever receives the configured target
    seen_targets = []

    async def trigger(target, token):
        seen_targets.append(target)
    await EndToEndProbe("DM-03", target="SBX-1", target_name="probe_issue", trigger=trigger, inbox=EventInbox()).run(timedelta(milliseconds=20))
    assert seen_targets == ["SBX-1"]
    # 3. without WRITE the probe cannot change anything
    read_only = make_client(access=frozenset({Access.READ, Access.SEARCH}))
    denied = await EndToEndProbe("DM-03", target="PROJ-1", target_name="probe_issue", trigger=issue_property_trigger(read_only), inbox=EventInbox()).run()
    assert not denied.ok and "PolicyDeniedError" in denied.detail
    # 4. with WRITE the user is still asked first; a refusal leaves the issue untouched
    refusing = make_client(confirm=lambda plan: False)
    declined = await EndToEndProbe("DM-03", target="PROJ-1", target_name="probe_issue", trigger=issue_property_trigger(refusing), inbox=EventInbox()).run()
    assert not declined.ok and len(refusing.seen) == 1
    with pytest.raises(NotFoundError):
        await refusing.jira.issues.get_property("PROJ-1", "atlian_assiant.probe")                # the property was never written


def test_sec_19_provisioning_and_probe_code_never_names_a_hardcoded_issue_or_page():
    import re
    from pathlib import Path
    src = (Path(__file__).parents[2] / "atlian_assiant" / "events" / "verify.py").read_text()
    assert not re.search(r"\b[A-Z][A-Z0-9]+-\d+\b", re.sub(r'""".*?"""', "", src, flags=re.S).replace("DM-", ""))


@pytest.mark.parametrize("method, access", [("POST", Access.WRITE), ("PUT", Access.WRITE), ("PATCH", Access.WRITE), ("DELETE", Access.DELETE)])
async def test_raw_request_cannot_bypass_the_access_policy_for_unsafe_methods(method, access):
    from atlian_assiant.core.errors import PolicyDeniedError
    ro = make_client(access=frozenset({Access.READ, Access.SEARCH}))
    with pytest.raises(PolicyDeniedError, match=access.value):
        await ro.raw.request(method, "/issue", product="jira", json={})
    assert (await ro.raw.request("GET", "/myself", product="jira")).status == 200          # safe methods stay READ
    declined = make_client(confirm=lambda plan: False)
    from atlian_assiant.core.errors import ConfirmationDeclinedError
    with pytest.raises(ConfirmationDeclinedError):
        await declined.raw.request(method, "/issue", product="jira", json={})            # and still need confirmation
    assert len(declined.seen) == 1
