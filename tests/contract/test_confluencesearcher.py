import pytest

from atlian_assiant.core.errors import InvalidRequestError
from atlian_assiant.protocols import ConfluenceSearcher

from .helpers import drain

pytestmark = [pytest.mark.layer_01]


@pytest.mark.anyio
async def test_conf_cql_search_returns_hits_and_paginates(impl):
    pages = await drain(impl(ConfluenceSearcher).search_cql, 'space = "ENG"', limit=2)
    assert [len(p.items) for p in pages] == [2, 1]
    hit = pages[0].items[0]
    assert (hit.content_id, hit.title, hit.type, hit.space_key) == ("123", "Runbook", "page", "Engineering")
    assert hit.last_modified is not None


@pytest.mark.anyio
async def test_conf_cql_search_empty_query_is_invalid(impl):
    with pytest.raises(InvalidRequestError) as exc:
        await impl(ConfluenceSearcher).search_cql("  ")
    assert exc.value.hint and "Example" in exc.value.hint


@pytest.mark.anyio
async def test_conf_cql_search_uses_v1_search_endpoint(harness):
    if harness.server is None:
        pytest.skip("wire-level assertion")
    await harness.impl(ConfluenceSearcher).search_cql('text ~ "x"', limit=5)
    sent = harness.server.requests[-1]
    assert sent["path"] == "/wiki/rest/api/search" and sent["query"]["cql"] == 'text ~ "x"'
