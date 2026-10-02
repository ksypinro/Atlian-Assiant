import pytest

from atlassian_client import BackendKind
from atlassian_client.protocols import SemanticSearcher
from atlassian_client.protocols.base import implementation_of
from atlassian_client.testing import FakeBackend

from .mcp_helpers import make_mcp

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]


@pytest.fixture(params=["fake", "mcp"])
def searcher(request):
    backend = FakeBackend(BackendKind.MCP) if request.param == "fake" else make_mcp()[0]
    return implementation_of(backend, SemanticSearcher)


async def test_semantic_search_returns_scored_hits(searcher):
    hits = await searcher.semantic_search("how do we rotate keys")
    assert hits and hits[0].title == "Runbook" and hits[0].product == "confluence" and 0 <= hits[0].score <= 1


async def test_limit_is_respected(searcher):
    assert len(await searcher.semantic_search("x", limit=1)) <= 1
