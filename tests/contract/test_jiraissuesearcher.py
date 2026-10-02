import pytest

from atlassian_client.core.errors import InvalidRequestError
from atlassian_client.protocols import JiraIssueSearcher

from .helpers import drain

pytestmark = [pytest.mark.layer_01]


@pytest.mark.anyio
async def test_jira_issue_search_pages_through_all_results(impl):
    pages = await drain(impl(JiraIssueSearcher).search_issues, "project = PROJ", limit=2)
    assert [len(p.items) for p in pages] == [2, 1]
    assert [i.key for p in pages for i in p.items] == ["PROJ-1", "PROJ-2", "PROJ-3"]
    assert pages[-1].next_cursor is None


@pytest.mark.anyio
async def test_jira_issue_search_single_large_page_has_no_cursor(impl):
    page = await impl(JiraIssueSearcher).search_issues("project = PROJ", limit=50)
    assert len(page.items) == 3 and not page.has_more


@pytest.mark.anyio
async def test_jira_issue_search_count(impl):
    assert await impl(JiraIssueSearcher).count_issues("project = PROJ") == 3


@pytest.mark.anyio
async def test_jira_issue_search_empty_jql_is_invalid_request(harness):
    if harness.name == "fake":
        pytest.skip("the fake does not parse JQL")
    with pytest.raises(InvalidRequestError):
        await harness.impl(JiraIssueSearcher).search_issues("   ")


@pytest.mark.anyio
async def test_jira_issue_search_uses_enhanced_endpoint_never_legacy(harness):
    if harness.server is None:
        pytest.skip("wire-level assertion")
    await harness.impl(JiraIssueSearcher).search_issues("project = PROJ", fields=["summary"], limit=1)
    sent = harness.server.requests[-1]
    assert (sent["method"], sent["path"]) == ("POST", "/rest/api/3/search/jql")
    assert sent["json"]["fields"] == ["summary"] and sent["json"]["maxResults"] == 1
    assert all(r["path"] != "/rest/api/3/search" for r in harness.server.requests)
