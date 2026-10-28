import anyio
import pytest

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.backends.rest_cloud.hydrate import search_then_hydrate
from atlian_assiant.testing.server import ScenarioServer


def rest(server):
    be = ClientFactory().backend(BackendKind.REST, AtlassianConfig(site="example.atlassian.net", email="a@b.c", api_token="tok-123456"),
                                 http=server.client())
    return be.confluence


@pytest.mark.anyio
async def test_hydrates_bodies_in_hit_order_without_asking_cql_to_expand_bodies():
    server = ScenarioServer()
    pages = await search_then_hydrate(rest(server), 'space = "ENG"', limit=10, batch_size=2)
    assert [p.id for p in pages] == ["123", "124", "125"]
    assert pages[0].body_markdown == "# Runbook\n\nRestart **nginx**"
    searches = [r for r in server.requests if r["path"] == "/wiki/rest/api/search"]
    assert len(searches) == 2                                             # 3 hits, batch of 2 -> two search pages
    assert all("expand" not in r["query"] and "body-format" not in r["query"] for r in searches)
    fetched = [r["path"] for r in server.requests if r["path"].startswith("/wiki/api/v2/pages/")]
    assert sorted(fetched) == [f"/wiki/api/v2/pages/{i}" for i in (123, 124, 125)]
    assert all(r["query"].get("body-format") == "storage" for r in server.requests if r["path"].startswith("/wiki/api/v2/pages/"))


@pytest.mark.anyio
async def test_respects_limit_and_stops_searching_early():
    server = ScenarioServer()
    pages = await search_then_hydrate(rest(server), "x", limit=2, batch_size=2)
    assert [p.id for p in pages] == ["123", "124"]
    assert len([r for r in server.requests if r["path"] == "/wiki/rest/api/search"]) == 1


@pytest.mark.anyio
async def test_fetch_concurrency_is_bounded():
    server = ScenarioServer()
    conf = rest(server)
    active = peak = 0
    orig = conf.get_content

    async def slow(*a, **k):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await anyio.sleep(0.02)
        try:
            return await orig(*a, **k)
        finally:
            active -= 1

    conf.get_content = slow                    # type: ignore[method-assign]
    pages = await search_then_hydrate(conf, "x", limit=3, batch_size=3, concurrency=2)
    assert len(pages) == 3 and peak == 2


@pytest.mark.anyio
async def test_hits_without_ids_are_skipped_and_empty_search_returns_nothing():
    server = ScenarioServer()
    server.raw["confluence"]["search"] = []
    assert await search_then_hydrate(rest(server), "x") == []
