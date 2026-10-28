import pytest

from atlian_assiant.core.errors import InvalidRequestError, NotFoundError, UnsupportedOperationError
from atlian_assiant.models.common import BodyInput
from atlian_assiant.models.jira import IssueDraft, IssueUpdate
from atlian_assiant.protocols import JiraIssueReader, JiraIssueWriter

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]


async def test_jira_issue_create_edit_round_trip(harness):
    w, r = harness.impl(JiraIssueWriter), harness.impl(JiraIssueReader)
    created = await w.create_issue(IssueDraft(project="PROJ", issue_type="Bug", summary="Brand new", description="Hello **world**", labels=["a"],
                                              assignee="bob@example.com", fields={"Story Points": 5}))
    assert created.key.startswith("PROJ-") and created.id
    issue = await r.get_issue(created.key)
    assert (issue.summary, issue.labels, issue.description_markdown) == ("Brand new", ["a"], "Hello **world**")
    await w.edit_issue(created.key, IssueUpdate(summary="Edited", labels_add=["b"], labels_remove=["a"]))
    issue = await r.get_issue(created.key)
    assert issue.summary == "Edited" and issue.labels == ["b"]


async def test_jira_issue_create_edit_resolves_field_names_and_users_on_the_wire(harness):
    if harness.server is None:
        pytest.skip("wire-level assertion")
    w = harness.impl(JiraIssueWriter)
    await w.create_issue(IssueDraft(project="PROJ", issue_type="Bug", summary="X", assignee="bob@example.com", fields={"Story Points": 8}, parent="PROJ-1"))
    sent = next(r for r in harness.server.requests if (r["method"], r["path"]) == ("POST", "/rest/api/3/issue"))
    f = sent["json"]["fields"]
    assert f["customfield_10016"] == 8 and f["assignee"] == {"accountId": "acc-bob"} and f["project"] == {"key": "PROJ"}
    assert f["issuetype"] == {"name": "Bug"} and f["parent"] == {"key": "PROJ-1"}


async def test_jira_issue_create_edit_validation_errors(harness):
    w = harness.impl(JiraIssueWriter)
    with pytest.raises(InvalidRequestError):
        await w.create_issue(IssueDraft(project="PROJ", issue_type="Bug", summary=" "))
    with pytest.raises(InvalidRequestError) as exc:
        await w.edit_issue("PROJ-1", IssueUpdate())
    assert exc.value.hint
    with pytest.raises(NotFoundError):
        await w.assign_issue("PROJ-404", None)


async def test_jira_issue_create_edit_assign_and_unassign(harness):
    w, r = harness.impl(JiraIssueWriter), harness.impl(JiraIssueReader)
    await w.assign_issue("PROJ-2", "ann@example.com")
    assert (await r.get_issue("PROJ-2")).assignee.display_name == "Ann Lee"
    await w.assign_issue("PROJ-2", None)
    assert (await r.get_issue("PROJ-2")).assignee is None


async def test_jira_bulk_create_creates_all_and_enforces_the_50_limit(harness):
    w = harness.impl(JiraIssueWriter)
    made = await w.bulk_create_issues([IssueDraft(project="PROJ", issue_type="Task", summary=f"T{i}") for i in range(3)])
    assert len(made) == 3 and len({m.key for m in made}) == 3
    with pytest.raises(InvalidRequestError):
        await w.bulk_create_issues([IssueDraft(project="PROJ", issue_type="Task", summary="x")] * 51)
    with pytest.raises(InvalidRequestError):
        await w.bulk_create_issues([])


async def test_jira_transition_by_name_with_comment_and_unknown_transition(harness):
    w, r = harness.impl(JiraIssueWriter), harness.impl(JiraIssueReader)
    await w.transition_issue("PROJ-1", "done", comment="Closing this")
    assert (await r.get_issue("PROJ-1")).status.name == "Done"
    assert (await r.list_comments("PROJ-1", limit=50)).items[-1].body_markdown == "Closing this"
    with pytest.raises(NotFoundError) as exc:
        await w.transition_issue("PROJ-1", "Teleport")
    assert exc.value.hint is not None


async def test_jira_comment_worklog_add_and_edit(harness):
    w, r = harness.impl(JiraIssueWriter), harness.impl(JiraIssueReader)
    c = await w.add_comment("PROJ-2", "Hello **team**")
    assert c.id and c.body_markdown == "Hello **team**"
    edited = await w.edit_comment("PROJ-2", c.id, "Changed")
    assert edited.body_markdown == "Changed"
    assert [x.body_markdown for x in (await r.list_comments("PROJ-2")).items] == ["Changed"]
    wl = await w.add_worklog("PROJ-2", "3h")
    assert wl.time_spent == "3h" and [x.time_spent for x in (await r.list_worklogs("PROJ-2")).items][-1] == "3h"
    with pytest.raises(NotFoundError):
        await w.add_comment("PROJ-404", "x")


async def test_jira_comment_adf_body_is_sent_as_adf_on_the_wire(harness):
    if harness.server is None:
        pytest.skip("wire-level assertion")
    await harness.impl(JiraIssueWriter).add_comment("PROJ-2", BodyInput(format="plain", value="plain text"), visibility={"type": "role", "value": "Developers"})
    sent = harness.server.requests[-1]
    assert sent["json"]["body"]["type"] == "doc" and sent["json"]["visibility"]["value"] == "Developers"


async def test_jira_links_write_issue_and_remote_links(harness):
    w, r = harness.impl(JiraIssueWriter), harness.impl(JiraIssueReader)
    await w.create_link("Relates", "PROJ-3", "PROJ-2")
    links = await r.list_links("PROJ-3")
    assert [(lk.issue_key, lk.direction) for lk in links] == [("PROJ-2", "outward")]
    rl = await w.create_remote_link("PROJ-2", "https://example.com/x", "Spec")
    assert rl.url == "https://example.com/x" and "Spec" in [x.title for x in await r.list_remote_links("PROJ-2")]
    with pytest.raises(NotFoundError):
        await w.create_link("Relates", "PROJ-3", "PROJ-404")


async def test_jira_attachment_upload_then_download(harness, tmp_path):
    w, r = harness.impl(JiraIssueWriter), harness.impl(JiraIssueReader)
    uploaded = await w.upload_attachment("PROJ-2", b"file body", filename="notes.txt")
    assert uploaded and uploaded[0].filename == "notes.txt"
    f = tmp_path / "local.txt"
    f.write_bytes(b"from path")
    again = await w.upload_attachment("PROJ-2", f)
    assert again[0].filename == "local.txt"
    names = {a.filename for a in await r.list_attachments("PROJ-2")}
    assert names == {"notes.txt", "local.txt"}
    got = await r.download_attachment(uploaded[0].id)
    assert got.data == b"file body"


async def test_jira_attachment_upload_sends_multipart_with_the_no_check_header(harness):
    if harness.server is None:
        pytest.skip("wire-level assertion")
    await harness.impl(JiraIssueWriter).upload_attachment("PROJ-2", b"x", filename="a.txt")
    sent = harness.server.requests[-1]
    assert sent["headers"]["x-atlassian-token"] == "no-check" and sent["headers"]["content-type"].startswith("multipart/form-data")


async def test_watch_and_property_and_hierarchy(harness):
    w, r = harness.impl(JiraIssueWriter), harness.impl(JiraIssueReader)
    await w.watch_issue("PROJ-2", watch=True, account_id="acc-bob")
    assert "Bob Ray" in {u.display_name for u in await r.list_watchers("PROJ-2")}
    await w.watch_issue("PROJ-2", watch=False, account_id="acc-bob")
    assert "Bob Ray" not in {u.display_name for u in await r.list_watchers("PROJ-2")}
    await w.set_property("PROJ-2", "my.app", {"n": 1})
    assert await r.get_property("PROJ-2", "my.app") == {"n": 1}
    if harness.name == "rest":
        with pytest.raises(UnsupportedOperationError) as exc:
            await w.convert_hierarchy("PROJ-2", new_issue_type="Epic")
        assert "MCP" in exc.value.hint
