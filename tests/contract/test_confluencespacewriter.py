import pytest

from atlassian_client.core.errors import UnsupportedOperationError
from atlassian_client.protocols import ConfluenceSpaceReader, ConfluenceSpaceWriter

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]


async def test_conf_content_write_create_space_and_read_back(harness):
    w, r = harness.impl(ConfluenceSpaceWriter), harness.impl(ConfluenceSpaceReader)
    sp = await w.create_space("NEW", "New space", description="A space")
    assert sp.key == "NEW" and sp.name == "New space"
    assert (await r.get_space("NEW")).id == sp.id


async def test_space_instructions_cannot_be_set_through_rest(harness):
    w = harness.impl(ConfluenceSpaceWriter)
    if harness.name == "rest":
        with pytest.raises(UnsupportedOperationError) as exc:
            await w.set_space_instructions("ENG", "Be nice")
        assert "MCP" in exc.value.hint
    else:
        await w.set_space_instructions("ENG", "Be nice")
