import pytest

from atlassian_client.core.errors import NotFoundError
from atlassian_client.protocols import JiraIssueReader

from .helpers import drain

pytestmark = [pytest.mark.layer_01]


@pytest.mark.anyio
async def test_jira_issue_get_returns_normalized_issue(impl):
    issue = await impl(JiraIssueReader).get_issue("PROJ-1")
    assert issue.key == "PROJ-1" and issue.id == "10001"
    assert issue.summary == "Login fails"
    assert issue.status and issue.status.name == "In Progress" and issue.status.category == "In Progress"
    assert issue.assignee and issue.assignee.display_name == "Ann Lee"
    assert issue.labels == ["backend"]
    assert issue.description_markdown == "Login fails with **500**"   # ADF converted to Markdown
    assert issue.fields["customfield_10016"] == 3


@pytest.mark.anyio
async def test_jira_issue_get_is_case_insensitive_and_missing_is_not_found(impl):
    reader = impl(JiraIssueReader)
    assert (await reader.get_issue("proj-1")).key == "PROJ-1"
    with pytest.raises(NotFoundError) as exc:
        await reader.get_issue("PROJ-999")
    assert exc.value.hint and "permission" in exc.value.hint.lower()


@pytest.mark.anyio
async def test_jira_comments_list_pages_with_opaque_cursors(impl):
    pages = await drain(impl(JiraIssueReader).list_comments, "PROJ-1", limit=2)
    assert [len(p.items) for p in pages] == [2, 1]
    assert pages[0].next_cursor and isinstance(pages[0].next_cursor, str)
    assert [c.id for p in pages for c in p.items] == ["c1", "c2", "c3"]
    assert pages[0].items[0].body_markdown == "Login fails with **500**"


@pytest.mark.anyio
async def test_jira_changelog_lists_field_changes(impl):
    page = await impl(JiraIssueReader).list_changelog("PROJ-1")
    item = page.items[0].items[0]
    assert (item.field, item.from_string, item.to_string) == ("status", "To Do", "In Progress")


@pytest.mark.anyio
async def test_jira_worklogs_list(impl):
    page = await impl(JiraIssueReader).list_worklogs("PROJ-1")
    assert page.items[0].time_spent == "2h" and page.items[0].time_spent_seconds == 7200


@pytest.mark.anyio
async def test_jira_transitions_list(impl):
    ts = await impl(JiraIssueReader).list_transitions("PROJ-1")
    assert ts[0].name == "Done" and ts[0].to_status and ts[0].to_status.name == "Done"


@pytest.mark.anyio
async def test_jira_attachment_download_metadata_and_content(impl, tmp_path):
    reader = impl(JiraIssueReader)
    files = await reader.list_attachments("PROJ-1")
    assert [(f.id, f.filename, f.mime_type) for f in files] == [("att1", "log.txt", "text/plain")]
    content = await reader.download_attachment("att1", extract_text=True)
    assert content.data == b"hello" and content.text == "hello" and content.size == 5
    saved = await reader.download_attachment("att1", dest=tmp_path / "out" / "log.txt")
    assert saved.data is None and (tmp_path / "out" / "log.txt").read_bytes() == b"hello"


@pytest.mark.anyio
async def test_jira_attachment_download_respects_max_bytes(impl):
    with pytest.raises(Exception) as exc:
        await impl(JiraIssueReader).download_attachment("att1", max_bytes=2)
    assert "max_bytes" in str(exc.value)


@pytest.mark.anyio
async def test_links_remote_links_watchers_and_properties(impl):
    reader = impl(JiraIssueReader)
    link = (await reader.list_links("PROJ-1"))[0]
    assert (link.link_type, link.direction, link.issue_key) == ("blocks", "outward", "PROJ-2")
    assert (await reader.list_remote_links("PROJ-1"))[0].url == "https://example.com/doc"
    assert {u.display_name for u in await reader.list_watchers("PROJ-1")} == {"Ann Lee", "Bob Ray"}
    assert await reader.get_property("PROJ-1", "my.app.state") == {"seen": True}
