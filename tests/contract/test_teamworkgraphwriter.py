import pytest

from atlian_assiant import BackendKind
from atlian_assiant.protocols import TeamworkGraphWriter
from atlian_assiant.protocols.base import implementation_of
from atlian_assiant.testing import FakeBackend

from .mcp_helpers import make_mcp

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]


@pytest.fixture(params=["fake", "mcp"])
def writer(request):
    backend = FakeBackend(BackendKind.MCP) if request.param == "fake" else make_mcp()[0]
    return implementation_of(backend, TeamworkGraphWriter)


async def test_add_relationship_succeeds_for_an_entity(writer):
    assert await writer.add_relationship("https://example.atlassian.net/browse/PROJ-1", "jira-work-item-links-jira-work-item", "PROJ-2") is None


async def test_add_relationship_needs_a_type_and_a_target(writer):
    from atlian_assiant.core.errors import InvalidRequestError

    with pytest.raises(InvalidRequestError):
        await writer.add_relationship("PROJ-1", "", "PROJ-2")
