import pytest

from atlian_assiant.core.errors import InvalidRequestError, UnsupportedOperationError
from atlian_assiant.protocols import ConfluenceContentReader, ConfluencePermissionWriter

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]


async def test_conf_content_write_permissions_add_remove_replace(harness):
    w, r = harness.impl(ConfluencePermissionWriter), harness.impl(ConfluenceContentReader)
    await w.add_permissions("124", [{"operation": "read", "group": "eng"}, {"operation": "update", "group": "leads"}])
    res = await r.get_restrictions("124")
    assert res.read_groups == ["eng"] and res.update_groups == ["leads"]
    await w.add_permissions("124", [{"operation": "read", "group": "ops"}])
    assert (await r.get_restrictions("124")).read_groups == ["eng", "ops"]
    await w.remove_permissions("124", [{"operation": "read", "group": "eng"}])
    assert (await r.get_restrictions("124")).read_groups == ["ops"]
    await w.replace_permissions("124", [{"operation": "update", "group": "admins"}])
    res = await r.get_restrictions("124")
    assert res.read_groups == [] and res.update_groups == ["admins"]


async def test_conf_content_write_permissions_reject_unknown_operations(harness):
    with pytest.raises(InvalidRequestError) as exc:
        await harness.impl(ConfluencePermissionWriter).add_permissions("124", [{"operation": "delete", "group": "x"}])
    assert "read" in exc.value.hint or "read" in str(exc.value)


async def test_public_links_need_mcp(harness):
    w = harness.impl(ConfluencePermissionWriter)
    if harness.name == "rest":
        with pytest.raises(UnsupportedOperationError):
            await w.set_public_link("123", enabled=True)
    else:
        await w.set_public_link("123", enabled=True)
