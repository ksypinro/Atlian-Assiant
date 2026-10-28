"""Opt-in live runs of the verify_dm_XX probes against the SANDBOX targets (design section 18, layer 14). Nightly in CI where credentials exist.

Run with:
    ATLASSIAN_LIVE_TESTS=1 ATLASSIAN_SITE=... ATLASSIAN_EMAIL=... ATLASSIAN_API_TOKEN=... \\
    ATLASSIAN_EVENTS_PROBE_ISSUE=SBX-1 ATLASSIAN_EVENTS_PROBE_PAGE=12345 pytest tests/live/test_events_live.py -q

Only the configured sandbox issue/page is ever changed (a label is added and removed; an issue property is set and cleared).
Skipped, never failed, when the variables are absent.
"""
import os
from datetime import UTC, datetime, timedelta

import pytest

from atlian_assiant import AsyncAtlassianClient
from atlian_assiant.capabilities import Access
from atlian_assiant.events import catalog
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.sources import JqlPollingSource
from atlian_assiant.events.stores import MemoryCheckpointStore
from atlian_assiant.events.verify import EventInbox, verify_dm_36
from atlian_assiant.policy import AccessPolicy, AutoApprove

pytestmark = [
    pytest.mark.layer_14, pytest.mark.anyio,
    pytest.mark.skipif(os.environ.get("ATLASSIAN_LIVE_TESTS") != "1" or not os.environ.get("ATLASSIAN_EVENTS_PROBE_ISSUE"),
                       reason="set ATLASSIAN_LIVE_TESTS=1 and ATLASSIAN_EVENTS_PROBE_ISSUE (a sandbox issue) to run live event probes"),
]


@pytest.mark.parametrize("dm", [m.id for m in catalog.DELIVERY_METHODS if m.implemented and m.phase == "P3"])
async def test_live_readiness_of_every_p3_method_is_reported_read_only(dm):
    async with AsyncAtlassianClient.from_env(preset="service") as client:
        report = await client.events.verify_method(dm)
        assert report.method_id == dm and report.checks


async def test_live_jql_polling_sees_the_probe_label_toggle():
    config = EventPlaneConfig()
    policy = AccessPolicy(allowed=frozenset({Access.READ, Access.SEARCH, Access.WRITE}))
    async with AsyncAtlassianClient.from_env(preset="service", policy=policy, confirm=AutoApprove()) as client:
        project = config.probe_issue.split("-")[0]
        source = await JqlPollingSource.from_client(client, f"project = {project}", MemoryCheckpointStore(), clock=lambda: datetime.now(UTC) - timedelta(minutes=1))
        await source.poll_once()
        inbox = EventInbox()

        async def pump() -> None:
            for ev in await source.poll_once():
                await inbox.push(ev)
        original = client.jira.issues.edit

        async def edit_then_poll(*a, **kw):
            result = await original(*a, **kw)
            await pump()
            return result
        client.jira.issues.edit = edit_then_poll                                   # type: ignore[method-assign]
        report = await verify_dm_36(client=client, config=config, inbox=inbox, timeout=timedelta(minutes=3))
        assert report.ok, report.detail
