"""Shared checks for the Slack methods (DM-21 Automation, DM-32 Jira app, DM-33 Confluence app)."""
from __future__ import annotations

from datetime import timedelta

from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.model import ChangeAction, EventSourceKind
from atlian_assiant.events.pipeline import EventPipeline
from atlian_assiant.events.sources import EventSource, SlackSocketModeSource
from atlian_assiant.events.stores import MemoryDedupStore
from atlian_assiant.events.verify import EventInbox

from .humansim import SITE, FakeSocket, slack_envelope
from .sitesim import SiteSim


def source(dm: str, envelopes, **kw):
    sock = FakeSocket(envelopes)
    return sock, SlackSocketModeSource(sock, channels=["C1"], project_keys=["PROJ"], method_id=dm, **kw)


async def check_consumer(dm: str) -> None:
    attachment = [{"title": "PROJ-5 Login fails", "title_link": f"{SITE}/browse/PROJ-5", "text": "Status: Done"}]
    sock, src = source(dm, [slack_envelope(ts="1.1", attachments=attachment, envelope_id="e1"), slack_envelope(channel="C9", ts="2.2", text="PROJ-6", envelope_id="e2"),
                            slack_envelope(ts="3.3", text="PROJ-7", bot_id="B-ME", envelope_id="e3"), slack_envelope(ts="4.4", text="deleted", subtype="message_deleted", envelope_id="e4"),
                            {"envelope_id": "e5", "type": "hello", "payload": {}}], own_bot_id="B-ME")
    assert isinstance(src, EventSource) and dm in src.method_ids and src.requires_public_ingress is False
    events = [e async for e in src.events()]
    assert [(e.event_id, e.entity_key, e.action, e.source, e.delivery.method_id) for e in events] == [("C1:1.1", "PROJ-5", ChangeAction.NOTIFIED, EventSourceKind.SLACK_SOCKET_MODE, dm)]
    assert sock.acked == ["e1", "e2", "e3", "e4", "e5"]                       # every envelope is acknowledged, or Slack redelivers it
    assert src.ignored == {"channel": 1, "self": 1, "type": 2}                # allow-list, own bot, other event types
    pipe, seen = EventPipeline([src], dedup=MemoryDedupStore()), []

    async def handle(ev):
        seen.append(ev.event_id)
    pipe.on()(handle)
    for ev in events * 2:                                                      # Slack may redeliver: channel:ts dedups
        await pipe.process(ev, src)
    assert seen == ["C1:1.1"]


async def check_verify(verify, dm: str, *, page: bool = False) -> None:
    sock, src = source(dm, [])
    inbox = EventInbox()

    async def notify(ref: str) -> None:
        sock.queue.append(slack_envelope(ts=f"9.{len(inbox.events)}", text=f"{SITE}/browse/{ref}" if not page else f"{SITE}/wiki/spaces/ENG/pages/{ref}/P"))
        for ev in [e async for e in src.events()]:
            await inbox.push(ev)
    site = SiteSim(on_issue=None if page else notify, on_page=notify if page else None)
    cfg = EventPlaneConfig(probe_issue="SBX-7", probe_page="9001")
    report = await verify(client=site, config=cfg, inbox=inbox, timeout=timedelta(seconds=2))
    assert report.ok and report.method_id == dm
