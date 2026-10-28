"""Shared consumer/verify checks for the email-based delivery methods (DM-20, 25..29): one ImapSource, six ways Atlassian fills the mailbox."""
from __future__ import annotations

from datetime import timedelta

from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.model import ChangeAction, EventSourceKind
from atlian_assiant.events.pipeline import EventPipeline
from atlian_assiant.events.sources import EventSource, ImapSource
from atlian_assiant.events.stores import MemoryCheckpointStore, MemoryDedupStore
from atlian_assiant.events.verify import EventInbox

from .humansim import SITE, FakeImap, mail
from .sitesim import SiteSim

SENDER = "jira@example.atlassian.net"


def make_source(dm: str, messages=None, **kw):
    imap = FakeImap(messages or [])
    return imap, ImapSource(imap, MemoryCheckpointStore(), allowed_senders=[SENDER, "@example.atlassian.net"], project_keys=["PROJ", "OPS"], method_id=dm, **kw)


async def check_consumer(dm: str, subject: str = "[JIRA] (PROJ-1) Login fails", body: str | None = None) -> None:
    body = body or f"Ann changed PROJ-1\n{SITE}/browse/PROJ-1"
    imap, src = make_source(dm, [mail(subject, body, message_id="<m1>")])
    assert isinstance(src, EventSource) and dm in src.method_ids and src.requires_public_ingress is False
    events = await src.poll_once()
    assert [(e.event_id, e.entity_key, e.action, e.source, e.delivery.method_id) for e in events] == [("<m1>", "PROJ-1", ChangeAction.NOTIFIED, EventSourceKind.EMAIL_IMAP, dm)]
    assert events[0].native_type == "email:jira" and imap.selected[0] == ("INBOX", True) and not imap.stored          # READ-ONLY folder: the mailbox is never modified
    assert await src.poll_once() == []                                                                                 # UID checkpoint: nothing is read twice
    imap.add(mail("again", f"{SITE}/browse/PROJ-2", message_id="<m2>"))
    assert [e.entity_key for e in await src.poll_once()] == ["PROJ-2"]
    fresh = ImapSource(imap, src.checkpoints, allowed_senders=[SENDER], method_id=dm)                                  # a restarted process resumes from the checkpoint
    assert await fresh.poll_once() == []


async def check_security_and_dedup(dm: str) -> None:
    imap, src = make_source(dm, [mail("phish", "PROJ-1 click here", sender="evil@attacker.example", message_id="<evil>"), mail("real", "PROJ-3", message_id="<ok>")])
    events = await src.poll_once()
    assert [e.event_id for e in events] == ["<ok>"] and src.ignored["sender"] == 1                                    # sender allow-list
    imap2, strict = make_source(dm, [mail("a", "PROJ-1", message_id="<a>", auth="mx; dkim=pass"), mail("b", "PROJ-1", message_id="<b>", auth="mx; dkim=fail"),
                                     mail("c", "PROJ-1", message_id="<c>")], require_authenticated=True)
    assert [e.event_id for e in await strict.poll_once()] == ["<a>"] and strict.ignored["unauthenticated"] == 2
    imap3, dup = make_source(dm, [mail("x", "PROJ-1", message_id="<same>"), mail("x", "PROJ-1", message_id="<same>")])
    pipe, seen = EventPipeline([dup], dedup=MemoryDedupStore()), []

    async def handle(ev):
        seen.append(ev.event_id)
    pipe.on()(handle)
    for ev in await dup.poll_once():
        await pipe.process(ev, dup)
    assert seen == ["<same>"] and pipe.stats.duplicates == 1                                                           # Message-ID dedup


async def check_digest(dm: str, text: str) -> None:
    imap, src = make_source(dm, [mail("Digest", text, message_id="<dig>")])
    events = await src.poll_once()
    assert len(events) >= 2 and len({e.event_id for e in events}) == len(events) and all(e.event_id.startswith("<dig>#") for e in events)


async def check_verify(verify, dm: str, *, page: bool = False) -> None:
    imap, src = make_source(dm)
    inbox = EventInbox()
    counter = {"n": 0}

    async def notify(ref: str) -> None:
        counter["n"] += 1
        imap.add(mail(f"change {ref}", f"Notification for {SITE}/browse/{ref}" if not page else f"{SITE}/wiki/spaces/ENG/pages/{ref}/Page", message_id=f"<p{counter['n']}>"))
        for ev in await src.poll_once():
            await inbox.push(ev)
    site = SiteSim(on_issue=None if page else notify, on_page=notify if page else None)
    cfg = EventPlaneConfig(probe_issue="SBX-7", probe_page="9001")
    report = await verify(client=site, config=cfg, inbox=inbox, timeout=timedelta(seconds=2))
    assert report.ok and report.method_id == dm
    nothing = await verify(client=SiteSim(), config=cfg, inbox=EventInbox(), timeout=timedelta(milliseconds=30))
    assert not nothing.ok and "no event" in nothing.detail
