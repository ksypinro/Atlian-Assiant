import pytest

from atlassian_client import BackendKind
from atlassian_client.protocols import TeamworkGraphReader
from atlassian_client.protocols.base import implementation_of
from atlassian_client.testing import FakeBackend

from .mcp_helpers import make_mcp

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]


@pytest.fixture(params=["fake", "mcp"])
def reader(request):
    backend = FakeBackend(BackendKind.MCP) if request.param == "fake" else make_mcp()[0]
    return implementation_of(backend, TeamworkGraphReader)


async def test_context_returns_related_objects_and_relationships(reader):
    ctx = await reader.get_context("https://example.atlassian.net/browse/PROJ-1")
    assert ctx.root.endswith("PROJ-1") and ctx.related and ctx.related[0].ari.startswith("ari:cloud:")


async def test_context_honours_max_items(reader):
    assert len((await reader.get_context("x", max_items=1)).related) <= 1


async def test_objects_by_ari_or_url_preserve_order(reader):
    assert [o.ari for o in await reader.get_objects(["a", "b"])] == ["a", "b"]
