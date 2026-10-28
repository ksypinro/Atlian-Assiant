"""Opt-in smoke tests against a REAL Atlassian site (design section 18, layer 17).

Run with:  ATLASSIAN_LIVE_TESTS=1 ATLASSIAN_SITE=... ATLASSIAN_EMAIL=... ATLASSIAN_API_TOKEN=... pytest tests/live -q
Read-only. Skipped (not failed) when the environment variable is absent.
"""
import os

import pytest

from atlian_assiant import AsyncAtlassianClient

pytestmark = [
    pytest.mark.layer_17, pytest.mark.anyio,
    pytest.mark.skipif(os.environ.get("ATLASSIAN_LIVE_TESTS") != "1", reason="set ATLASSIAN_LIVE_TESTS=1 to run against a real site"),
]


async def test_whoami_and_describe_against_a_real_site():
    async with AsyncAtlassianClient.from_env(preset="service") as client:
        me = await client.whoami()
        assert me.account_id
        assert client.describe()["backends"]["rest"].startswith("ready")


async def test_search_and_read_one_issue_and_one_page():
    async with AsyncAtlassianClient.from_env(preset="service") as client:
        page = await client.jira.issues.search("order by updated DESC", limit=1)
        if page.items:
            issue = await client.jira.issues.get(page.items[0].key)
            assert issue.key == page.items[0].key
        hits = await client.confluence.search.cql("type = page order by lastmodified desc", limit=1)
        if hits.items and hits.items[0].content_id:
            assert (await client.confluence.content.get(hits.items[0].content_id)).id


async def test_health_reports_every_backend():
    async with AsyncAtlassianClient.from_env() as client:
        assert (await client.health())["rest"].startswith("ready")
