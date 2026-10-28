"""Shared checks for the Teams methods (DM-22 Automation, DM-34 Jira app): Graph delta polling."""
from __future__ import annotations

from datetime import timedelta

from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.model import ChangeAction, EventSourceKind
from atlian_assiant.events.sources import EventSource, TeamsChannelPollingSource
from atlian_assiant.events.stores import MemoryCheckpointStore
from atlian_assiant.events.verify import EventInbox

from .humansim import SITE, graph_http
from .sitesim import SiteSim


def msg(mid: str, content: str, html: bool = False, deleted=None):
    return {"id": mid, "body": {"contentType": "html" if html else "text", "content": content}, "deletedDateTime": deleted}


def source(dm: str, pages, cp=None):
    http, state = graph_http(pages)

    async def token() -> str:
        return "graph-token"
    return state, TeamsChannelPollingSource(http, token, cp or MemoryCheckpointStore(), team_id="T1", channel_id="CH1", project_keys=["PROJ"], method_id=dm)


async def check_consumer(dm: str) -> None:
    cp = MemoryCheckpointStore()
    page1 = {"value": [msg("m1", f'<a href="{SITE}/browse/PROJ-5">PROJ-5</a> Done', html=True), msg("m2", "PROJ-6", deleted="2026-09-01T00:00:00Z")], "@odata.nextLink": "https://graph/next?skip=1"}
    page2 = {"value": [msg("m3", "PROJ-7 assigned")], "@odata.deltaLink": "https://graph/delta?token=abc"}
    state, src = source(dm, [page1, page2], cp)
    assert isinstance(src, EventSource) and dm in src.method_ids and src.requires_public_ingress is False
    events = await src.poll_once()
    assert [(e.event_id, e.entity_key, e.action, e.source, e.delivery.method_id) for e in events] == [
        ("m1", "PROJ-5", ChangeAction.NOTIFIED, EventSourceKind.TEAMS_GRAPH, dm), ("m3", "PROJ-7", ChangeAction.NOTIFIED, EventSourceKind.TEAMS_GRAPH, dm)]   # deleted message skipped
    assert state["requests"][0][0].endswith("/teams/T1/channels/CH1/messages/delta") and state["requests"][1][0] == "https://graph/next?skip=1"
    assert all(r[1] == "Bearer graph-token" for r in state["requests"]) and await cp.load(src.stream) == "https://graph/delta?token=abc"
    state["pages"].append({"value": [msg("m4", "PROJ-8")], "@odata.deltaLink": "https://graph/delta?token=def"})
    assert [e.entity_key for e in await src.poll_once()] == ["PROJ-8"] and state["requests"][2][0] == "https://graph/delta?token=abc"      # resumes from the delta link


async def check_verify(verify, dm: str) -> None:
    state, src = source(dm, [])
    inbox = EventInbox()

    async def notify(ref: str) -> None:
        state["pages"].append({"value": [msg(f"p{len(inbox.events)}", f"{SITE}/browse/{ref}")], "@odata.deltaLink": "https://graph/delta?x"})
        for ev in await src.poll_once():
            await inbox.push(ev)
    report = await verify(client=SiteSim(on_issue=notify), config=EventPlaneConfig(probe_issue="SBX-7"), inbox=inbox, timeout=timedelta(seconds=2))
    assert report.ok and report.method_id == dm
