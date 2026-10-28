"""DM-48: MCP v2 is not a subscription API: polling THROUGH MCP tools (JqlPollingSource / CqlPollingSource with transport=MCP, verify_dm_48)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.capabilities import Access
from atlian_assiant.events.catalog import Ingress, MethodKind, method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.sources import CqlPollingSource, JqlPollingSource
from atlian_assiant.events.stores import MemoryCheckpointStore
from atlian_assiant.events.verify import EventInbox, verify_dm_48
from atlian_assiant.policy import AccessPolicy
from atlian_assiant.testing import FakeBackend

from .sitesim import SiteSim
from .test_dm_36 import T0, FakeJira
from .test_dm_37 import FakeConfluence

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]


def test_dm_48_catalog_entry_reuses_the_pollers_and_needs_no_ingress():
    m = method("DM-48")
    assert m.kind is MethodKind.MACHINE_PULL and m.ingress is Ingress.NONE and m.provisioner is None and m.consumer.endswith("JqlPollingSource")
    assert "DM-48" in JqlPollingSource.method_ids and "DM-48" in CqlPollingSource.method_ids


async def test_dm_48_pollers_with_the_mcp_transport_label_events_as_dm_48():
    jira, conf = FakeJira(), FakeConfluence()
    j = JqlPollingSource(jira.search, "project = PROJ", MemoryCheckpointStore(), clock=lambda: T0, transport=BackendKind.MCP)
    c = CqlPollingSource(conf.search, "space = ENG", MemoryCheckpointStore(), clock=lambda: T0, transport=BackendKind.MCP)
    await j.poll_once()
    await c.poll_once()
    jira.add("PROJ-1", T0 + timedelta(minutes=1))
    conf.add("100", T0 + timedelta(minutes=1))
    for ev in (await j.poll_once()) + (await c.poll_once()):
        assert ev.delivery.method_id == "DM-48" and ev.delivery.transport_backend is BackendKind.MCP


async def test_dm_48_polling_works_when_only_mcp_credentials_exist_by_forcing_the_backend():
    """With ONLY an MCP backend registered, the poller still works: the search is routed through MCP (policy: read + search)."""
    mcp = FakeBackend(BackendKind.MCP)
    client = ClientFactory().build_async(AtlassianConfig(site="example.atlassian.net"), backends=[], preset="agent", policy=AccessPolicy(allowed=frozenset({Access.READ, Access.SEARCH})),
                                         extra_backends={BackendKind.MCP: mcp})
    cp = MemoryCheckpointStore()
    src = await JqlPollingSource.from_client(client, "project = PROJ", cp, transport=BackendKind.MCP, timezone="UTC", clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    assert await src.poll_once() == [] and src.method_id == "DM-48"                      # baseline sweep
    later = await JqlPollingSource.from_client(client, "project = PROJ", cp, transport=BackendKind.MCP, timezone="UTC", clock=lambda: datetime(2026, 9, 3, tzinfo=UTC))
    events = await later.poll_once()
    assert events and all(e.delivery.transport_backend is BackendKind.MCP and e.delivery.method_id == "DM-48" for e in events)
    page = await client.jira.issues.search("project = PROJ", limit=5, backend=BackendKind.MCP)
    assert page.items and page.items[0].source.backend is BackendKind.MCP


async def test_dm_48_there_is_no_mcp_subscription_and_the_guard_says_so():
    from atlian_assiant.core.errors import UnsupportedDeliveryPathError
    from atlian_assiant.events import guards
    with pytest.raises(UnsupportedDeliveryPathError) as exc:
        guards.mcp_subscription()
    assert "transport=BackendKind.MCP" in exc.value.hint


async def test_dm_48_end_to_end_verify_sees_the_probe_through_the_mcp_poller():
    jira = FakeJira()
    src = JqlPollingSource(jira.search, "project = PROJ", MemoryCheckpointStore(), clock=lambda: T0, transport=BackendKind.MCP)
    await src.poll_once()
    inbox = EventInbox()

    async def on_issue(key):
        jira.add(key, T0 + timedelta(minutes=2))
        for ev in await src.poll_once():
            await inbox.push(ev)
    report = await verify_dm_48(client=SiteSim(on_issue=on_issue), config=EventPlaneConfig(probe_issue="PROJ-9"), inbox=inbox, timeout=timedelta(seconds=2))
    assert report.ok and report.method_id == "DM-48"
