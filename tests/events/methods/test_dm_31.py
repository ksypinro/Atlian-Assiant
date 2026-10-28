"""DM-31: Confluence RSS (RssFeedProvisioner.confluence, RssSource over Atom, verify_dm_31)."""
from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from atlian_assiant.events.catalog import method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.model import EntityType
from atlian_assiant.events.provision.rss import RssFeedProvisioner
from atlian_assiant.events.sources import RssSource
from atlian_assiant.events.stores import MemoryCheckpointStore
from atlian_assiant.events.verify import EventInbox, verify_dm_31

from .humansim import ATOM_XML, SITE, rss_http
from .sitesim import SiteSim

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]


def test_dm_31_catalog_entry():
    assert {p.value for p in method("DM-31").products} == {"confluence"}


async def test_dm_31_provision_builds_a_private_feed_url_for_the_chosen_spaces_and_types():
    prov = RssFeedProvisioner.confluence("example.atlassian.net", spaces=["ENG", "OPS"], time_span_days=2)
    plan = await prov.plan()
    q = parse_qs(urlsplit(plan.outputs["feed_url"]).query)
    assert plan.method_id == "DM-31" and q["types"] == ["page", "blogpost"] and q["spaces"] == ["ENG", "OPS"] and q["publicFeed"] == ["false"] and q["timeSpan"] == ["2"]
    assert any("VER-20" in w for w in plan.warnings)


async def test_dm_31_consumer_reads_atom_entries_and_resolves_the_page_from_the_link():
    http, state = rss_http(ATOM_XML)
    src = RssSource(http, f"{SITE}/wiki/createrssfeed.action?types=page", MemoryCheckpointStore(), method_id="DM-31")
    events = await src.poll_once()
    assert [(e.event_id, e.entity_type, e.entity_id, e.delivery.method_id) for e in events] == [("a1", EntityType.PAGE, "4455", "DM-31")]


async def test_dm_31_end_to_end_verify():
    http, state = rss_http(ATOM_XML)
    src = RssSource(http, f"{SITE}/wiki/createrssfeed.action", MemoryCheckpointStore(), method_id="DM-31")
    inbox = EventInbox()

    async def notify(page_id):
        state["etag"] = f'"v{len(inbox.events) + 2}"'
        state["xml"] = ATOM_XML.replace("4455", page_id).replace("<id>a1</id>", f"<id>a-{page_id}-{len(inbox.events)}</id>")
        for ev in await src.poll_once():
            await inbox.push(ev)
    report = await verify_dm_31(client=SiteSim(on_page=notify), config=EventPlaneConfig(probe_page="9001"), inbox=inbox, timeout=timedelta(seconds=2))
    assert report.ok and report.method_id == "DM-31"
