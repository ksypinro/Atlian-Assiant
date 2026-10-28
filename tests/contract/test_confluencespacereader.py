import pytest

from atlian_assiant.core.errors import NotFoundError
from atlian_assiant.protocols import ConfluenceSpaceReader

pytestmark = [pytest.mark.layer_01]


@pytest.mark.anyio
async def test_spaces_list_get_by_key_and_id(impl):
    r = impl(ConfluenceSpaceReader)
    assert [s.key for s in (await r.list_spaces()).items] == ["ENG", "OPS"]
    assert [s.key for s in (await r.list_spaces(keys=["OPS"])).items] == ["OPS"]
    assert (await r.get_space("ENG")).id == "9001"
    assert (await r.get_space("9001")).key == "ENG"
    with pytest.raises(NotFoundError):
        await r.get_space("NOPE")


@pytest.mark.anyio
async def test_space_instructions_are_unsupported_on_rest_so_the_router_can_fall_back_to_mcp(impl):
    from atlian_assiant.core.errors import UnsupportedOperationError

    r = impl(ConfluenceSpaceReader)
    with pytest.raises(UnsupportedOperationError):
        await r.get_space_instructions("ENG")


@pytest.mark.anyio
async def test_space_permissions_page(impl):
    assert (await impl(ConfluenceSpaceReader).list_space_permissions("ENG")).items == []
