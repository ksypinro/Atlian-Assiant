import pytest

from atlassian_client.core.errors import AmbiguousReferenceError, InvalidRequestError, NotFoundError
from atlassian_client.protocols import JiraUserDirectory

pytestmark = [pytest.mark.layer_01]


@pytest.mark.anyio
async def test_jira_users_myself_get_search(impl):
    r = impl(JiraUserDirectory)
    assert (await r.myself()).account_id == "acc-ann"
    assert (await r.get_user("acc-bob")).display_name == "Bob Ray"
    assert [u.display_name for u in await r.search_users("lee")] == ["Ann Lee"]


@pytest.mark.anyio
async def test_jira_users_lookup_account_id_exact_ambiguous_and_missing(impl):
    r = impl(JiraUserDirectory)
    assert await r.lookup_account_id("ann@example.com") == "acc-ann"
    with pytest.raises(AmbiguousReferenceError):
        await r.lookup_account_id("example.com")
    with pytest.raises(NotFoundError):
        await r.lookup_account_id("nobody")


@pytest.mark.anyio
async def test_jira_users_assignable_needs_scope(impl):
    r = impl(JiraUserDirectory)
    assert len(await r.assignable_users(project="PROJ")) == 2
    with pytest.raises(InvalidRequestError):
        await r.assignable_users()
