import pytest

from atlian_assiant.core.errors import NotFoundError
from atlian_assiant.protocols import JiraIssueDeleter, JiraIssueReader, JiraIssueWriter

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]


async def test_jira_delete_issue_then_it_is_gone(harness):
    d, r = harness.impl(JiraIssueDeleter), harness.impl(JiraIssueReader)
    await d.delete_issue("PROJ-3")
    with pytest.raises(NotFoundError):
        await r.get_issue("PROJ-3")
    with pytest.raises(NotFoundError):
        await d.delete_issue("PROJ-3")


async def test_jira_delete_comment_and_attachment(harness):
    d, r, w = harness.impl(JiraIssueDeleter), harness.impl(JiraIssueReader), harness.impl(JiraIssueWriter)
    await d.delete_comment("PROJ-1", "c1")
    assert [c.id for c in (await r.list_comments("PROJ-1", limit=50)).items] == ["c2", "c3"]
    with pytest.raises(NotFoundError):
        await d.delete_comment("PROJ-1", "c1")
    await d.delete_attachment("att1")
    assert await r.list_attachments("PROJ-1") == []
    with pytest.raises(NotFoundError):
        await d.delete_attachment("att1")


async def test_jira_delete_sends_delete_subtasks_flag(harness):
    if harness.server is None:
        pytest.skip("wire-level assertion")
    await harness.impl(JiraIssueDeleter).delete_issue("PROJ-2", delete_subtasks=True)
    sent = harness.server.requests[-1]
    assert (sent["method"], sent["path"], sent["query"]) == ("DELETE", "/rest/api/3/issue/PROJ-2", {"deleteSubtasks": "true"})
