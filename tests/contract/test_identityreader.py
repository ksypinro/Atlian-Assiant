import pytest

from atlassian_client.protocols import IdentityReader

pytestmark = [pytest.mark.layer_01]


@pytest.mark.anyio
async def test_identity_whoami_and_accessible_resources(impl):
    r = impl(IdentityReader)
    me = await r.whoami()
    assert me.account_id == "acc-ann" and me.display_name == "Ann Lee"
    assert (await r.accessible_resources())[0].id == "cloud-1"
