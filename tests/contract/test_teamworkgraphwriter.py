import pytest

from atlassian_client import BackendKind
from atlassian_client.protocols import TeamworkGraphWriter
from atlassian_client.protocols.base import implementation_of
from atlassian_client.testing import FakeBackend

from .mcp_helpers import make_mcp

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]


@pytest.fixture(params=["fake", "mcp"])
def writer(request):
    backend = FakeBackend(BackendKind.MCP) if request.param == "fake" else make_mcp()[0]
    return implementation_of(backend, TeamworkGraphWriter)


async def test_add_context_succeeds_for_an_entity(writer):
    assert await writer.add_context("https://example.atlassian.net/browse/PROJ-1", {"note": "related to the outage"}) is None
