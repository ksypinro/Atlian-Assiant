"""DM-23: Automation -> Twilio SMS (AutomationRuleProvisioner(action=TWILIO), TwilioMessageLogSource, verify_dm_23)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from atlian_assiant.core.errors import InvalidRequestError
from atlian_assiant.events.automation import AutomationAction, AutomationRuleProvisioner, AutomationRuleTemplate
from atlian_assiant.events.catalog import method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.model import ChangeAction, EventSourceKind
from atlian_assiant.events.sources import EventSource, TwilioMessageLogSource
from atlian_assiant.events.stores import MemoryCheckpointStore
from atlian_assiant.events.verify import EventInbox, verify_dm_23

from .humansim import SITE, twilio_http
from .sitesim import SiteSim

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]


def sms(sid, body, sent="Mon, 01 Sep 2026 10:00:00 +0000", status="delivered", code=None):
    return {"sid": sid, "body": body, "date_sent": sent, "status": status, "error_code": code}


def source(messages, cp=None):
    http, state = twilio_http(messages)
    return state, TwilioMessageLogSource(http, cp or MemoryCheckpointStore(), account_sid="AC1", auth_token="tok", from_number="+15551230000", project_keys=["PROJ"])


def test_dm_23_catalog_entry():
    assert method("DM-23").consumer.endswith("TwilioMessageLogSource")


async def test_dm_23_provision_requires_destination_numbers_and_keeps_twilio_credentials_inside_automation(tmp_path):
    t = AutomationRuleTemplate(name="SMS", product="jira", trigger="issue_created", action=AutomationAction.TWILIO, action_config={"to": "+15551239999"})
    plan = await AutomationRuleProvisioner(t, output_dir=tmp_path).plan()
    assert plan.method_id == "DM-23" and any("credentials live inside Automation" in w for w in plan.warnings)
    with pytest.raises(InvalidRequestError, match="to"):
        await AutomationRuleProvisioner(AutomationRuleTemplate(name="x", product="jira", trigger="issue_created", action=AutomationAction.TWILIO), output_dir=tmp_path).plan()


async def test_dm_23_consumer_polls_the_message_log_for_the_sending_number_and_checkpoints():
    cp = MemoryCheckpointStore()
    state, src = source([sms("SM1", "[Jira] PROJ-4 assigned to you"), sms("SM2", "no issue here", sent="Mon, 01 Sep 2026 10:05:00 +0000")], cp)
    assert isinstance(src, EventSource) and src.method_ids == {"DM-23"}
    events = await src.poll_once()
    assert [(e.event_id, e.entity_key, e.action, e.source) for e in events] == [("SM1", "PROJ-4", ChangeAction.NOTIFIED, EventSourceKind.TWILIO_LOG), ("SM2", None, ChangeAction.NOTIFIED, EventSourceKind.TWILIO_LOG)]
    params, auth = state["requests"][0]
    assert params["From"] == "+15551230000" and "DateSent>" not in params and auth.startswith("Basic ")
    assert await cp.load(src.stream) == "2026-09-01T10:05:00+00:00"
    await src.poll_once()
    assert state["requests"][1][0]["DateSent>"] == "2026-09-01T10:05:00+00:00"                  # only newer messages are asked for


async def test_dm_23_delivery_failures_are_recorded_as_an_audit_but_still_emitted():
    state, src = source([sms("SM3", "PROJ-1", status="undelivered", code=30003)])
    events = await src.poll_once()
    assert len(events) == 1 and src.delivery_failures == [{"sid": "SM3", "status": "undelivered", "error_code": 30003}]


async def test_dm_23_end_to_end_verify():
    state, src = source([])
    inbox = EventInbox()

    async def notify(ref):
        state["messages"].append(sms(f"SMp{len(inbox.events)}", f"{SITE}/browse/{ref}", sent=f"Mon, 01 Sep 2026 11:0{len(inbox.events)}:00 +0000"))
        for ev in await src.poll_once():
            await inbox.push(ev)
    report = await verify_dm_23(client=SiteSim(on_issue=notify), config=EventPlaneConfig(probe_issue="SBX-7"), inbox=inbox, timeout=timedelta(seconds=2))
    assert report.ok and report.method_id == "DM-23"
