"""DM-30: Jira RSS (RssFeedProvisioner.jira_filter, RssSource, verify_dm_30)."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from atlian_assiant.core.errors import InvalidRequestError
from atlian_assiant.events.catalog import method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.model import ChangeAction, EventSourceKind
from atlian_assiant.events.provision.base import EventProvisioner
from atlian_assiant.events.provision.rss import RssFeedProvisioner
from atlian_assiant.events.sources import EventSource, RssSource
from atlian_assiant.events.stores import MemoryCheckpointStore
from atlian_assiant.events.verify import EventInbox, verify_dm_30
from atlian_assiant.policy import CallbackConfirmation

from .humansim import RSS_XML, SITE, rss_http
from .sitesim import SiteSim

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
URL = f"{SITE}/sr/jira.issueviews:searchrequest-rss/10042/SearchRequest-10042.xml?tempMax=100"


def source(xml=RSS_XML, cp=None, **kw):
    http, state = rss_http(xml)
    return state, RssSource(http, URL, cp or MemoryCheckpointStore(), auth=("ann@example.com", "token"), project_keys=["PROJ"], **kw)


def test_dm_30_catalog_entry():
    assert method("DM-30").consumer.endswith("RssSource") and isinstance(RssFeedProvisioner("x"), EventProvisioner)


async def test_dm_30_provision_builds_the_feed_url_without_credentials_in_it():
    prov = RssFeedProvisioner.jira_filter("example.atlassian.net", 10042)
    plan = await prov.plan()
    assert plan.method_id == "DM-30" and plan.outputs["feed_url"] == URL and "email" in plan.outputs["auth"] and any("VER-20" in w for w in plan.warnings)
    assert "@" not in URL.split("//", 1)[1].split("/")[0] and "token" not in URL
    out = await prov.apply(plan, confirm=CallbackConfirmation(lambda p: True))
    assert "never paste the token into the URL" in out["instructions"] and "Share the underlying filter" in out["instructions"] if "instructions" in out else out["feed_url"] == URL
    with pytest.raises(InvalidRequestError, match="no feed"):
        await RssFeedProvisioner("x").plan()


async def test_dm_30_consumer_polls_with_basic_auth_conditional_get_and_dedups_by_guid():
    state, src = source()
    assert isinstance(src, EventSource) and src.method_ids == {"DM-30", "DM-31"}
    events = await src.poll_once()
    assert [(e.event_id, e.entity_key, e.action, e.source) for e in events] == [("g1", "PROJ-1", ChangeAction.NOTIFIED, EventSourceKind.RSS), ("g2", "PROJ-2", ChangeAction.NOTIFIED, EventSourceKind.RSS)]
    assert "if-none-match" not in state["requests"][0] and state["requests"][0]["authorization"].startswith("Basic ")
    assert await src.poll_once() == [] and src.not_modified == 1 and state["requests"][1]["if-none-match"] == '"v1"' and "if-modified-since" in state["requests"][1]


async def test_dm_30_changed_feeds_emit_only_new_entries_and_state_survives_a_restart():
    cp = MemoryCheckpointStore()
    state, src = source(cp=cp)
    await src.poll_once()
    state["etag"], state["xml"] = '"v2"', RSS_XML.replace("</channel>", '<item><title>[PROJ-3] New</title><link>https://example.atlassian.net/browse/PROJ-3</link><guid>g3</guid></item></channel>')
    assert [e.entity_key for e in await src.poll_once()] == ["PROJ-3"]
    http, state2 = rss_http(state["xml"], etag='"v2"')
    again = RssSource(http, URL, cp, project_keys=["PROJ"])
    assert await again.poll_once() == [] and state2["requests"][0]["if-none-match"] == '"v2"'
    assert json.loads(await cp.load(again.stream))["seen"] == ["g1", "g2", "g3"]


async def test_dm_30_a_non_feed_response_is_reported_clearly():
    http, state = rss_http("<html><body>Please log in</body></html>")
    with pytest.raises(InvalidRequestError, match="not an RSS/Atom feed"):
        await RssSource(http, URL, MemoryCheckpointStore()).poll_once()


async def test_dm_30_end_to_end_verify():
    state, src = source()
    inbox = EventInbox()

    async def notify(ref):
        state["etag"] = f'"v{len(inbox.events) + 2}"'
        state["xml"] = RSS_XML.replace("PROJ-1", ref).replace("g1", f"g-{ref}-{len(inbox.events)}")
        for ev in await src.poll_once():
            await inbox.push(ev)
    report = await verify_dm_30(client=SiteSim(on_issue=notify), config=EventPlaneConfig(probe_issue="SBX-7"), inbox=inbox, timeout=timedelta(seconds=2))
    assert report.ok and report.method_id == "DM-30"
