import pytest

from atlassian_client.core.errors import NotFoundError
from atlassian_client.protocols import ConfluenceContentDeleter, ConfluenceContentReader

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]


async def test_conf_content_delete_page_then_gone(harness):
    d, r = harness.impl(ConfluenceContentDeleter), harness.impl(ConfluenceContentReader)
    await d.delete_content("125")
    with pytest.raises(NotFoundError):
        await r.get_content("125")
    with pytest.raises(NotFoundError):
        await d.delete_content("125")


async def test_conf_content_delete_comments_and_attachments(harness):
    d, r = harness.impl(ConfluenceContentDeleter), harness.impl(ConfluenceContentReader)
    await d.delete_comment("fc1")
    await d.delete_comment("ic1")
    assert (await r.list_comments("123")).items == []
    with pytest.raises(NotFoundError):
        await d.delete_comment("fc1")
    await d.delete_attachment("cat1")
    assert [a.id for a in (await r.list_attachments("123")).items] == ["cat2"]
    with pytest.raises(NotFoundError):
        await d.delete_attachment("cat1")


async def test_conf_content_delete_passes_the_purge_flag(harness):
    if harness.server is None:
        pytest.skip("wire-level assertion")
    await harness.impl(ConfluenceContentDeleter).delete_content("124", purge=True)
    sent = harness.server.requests[-1]
    assert (sent["method"], sent["path"], sent["query"]) == ("DELETE", "/wiki/api/v2/pages/124", {"purge": "true"})
