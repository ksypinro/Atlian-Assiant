"""One test group per write operation x MCP cell of design section 11 (layer 4: MCP)."""
import pytest

from atlian_assiant.core.errors import InvalidRequestError, NotFoundError, UnsupportedOperationError
from atlian_assiant.models.confluence import ContentDraft, ContentUpdate, Restrictions
from atlian_assiant.models.jira import IssueDraft, IssueUpdate, SprintChange, VersionChange
from atlian_assiant.protocols import (
    ConfluenceContentReader,
    ConfluenceContentWriter,
    ConfluencePermissionWriter,
    ConfluenceSpaceWriter,
    ConfluenceSpecialContent,
    JiraAdmin,
    JiraAgileReader,
    JiraAgileWriter,
    JiraIssueDeleter,
    JiraIssueReader,
    JiraIssueWriter,
    JiraProjectReader,
    JiraProjectWriter,
    JsmReader,
    JsmWriter,
    TeamworkGraphWriter,
)
from atlian_assiant.protocols.base import implementation_of

from .mcp_helpers import make_mcp

pytestmark = [pytest.mark.layer_04, pytest.mark.anyio]


def parts(*protos):
    mcp, transport = make_mcp()
    return (*(implementation_of(mcp, p) for p in protos), transport)


async def test_jira_issue_create_edit_round_trip_through_mcp():
    w, r, t = parts(JiraIssueWriter, JiraIssueReader)
    created = await w.create_issue(IssueDraft(project="PROJ", issue_type="Bug", summary="From MCP", description="Hello **there**", labels=["a"], assignee="acc-bob"))
    issue = await r.get_issue(created.key)
    assert (issue.summary, issue.labels, issue.description_markdown, issue.assignee.display_name) == ("From MCP", ["a"], "Hello **there**", "Bob Ray")
    await w.edit_issue(created.key, IssueUpdate(summary="Edited", labels_add=["b"], labels_remove=["a"]))
    assert (await r.get_issue(created.key)).labels == ["b"]
    await w.assign_issue(created.key, None)
    assert (await r.get_issue(created.key)).assignee is None
    assert t.calls[0][0] == "getAccessibleAtlassianResources" and next(c for c in t.calls if c[0] == "createJiraIssue")[1]["projectKey"] == "PROJ"
    with pytest.raises(InvalidRequestError):
        await w.edit_issue(created.key, IssueUpdate())
    with pytest.raises(InvalidRequestError):
        await w.create_issue(IssueDraft(project="PROJ", issue_type="Bug", summary=" "))


async def test_jira_bulk_create_is_rest_only():
    w, _ = parts(JiraIssueWriter)
    with pytest.raises(UnsupportedOperationError):
        await w.bulk_create_issues([IssueDraft(project="PROJ", issue_type="Task", summary="x")])


async def test_jira_transition_by_name_and_unknown():
    w, r, _ = parts(JiraIssueWriter, JiraIssueReader)
    await w.transition_issue("PROJ-1", "done", comment="Closing")
    assert (await r.get_issue("PROJ-1")).status.name == "Done"
    assert (await r.list_comments("PROJ-1", limit=50)).items[-1].body_markdown == "Closing"
    with pytest.raises(NotFoundError) as exc:
        await w.transition_issue("PROJ-1", "Teleport")
    assert "Done" in exc.value.hint


async def test_jira_comment_worklog_add_through_mcp():
    w, r, _ = parts(JiraIssueWriter, JiraIssueReader)
    c = await w.add_comment("PROJ-2", "Hi **team**")
    assert c.body_markdown == "Hi **team**"
    assert (await w.edit_comment("PROJ-2", c.id, "Changed")).body_markdown == "Changed"
    assert (await w.add_worklog("PROJ-2", "3h")).time_spent == "3h"
    assert [x.time_spent for x in (await r.list_worklogs("PROJ-2")).items] == ["3h"]


async def test_jira_links_write_issue_and_remote_links_through_mcp():
    w, r, _ = parts(JiraIssueWriter, JiraIssueReader)
    await w.create_link("Relates", "PROJ-3", "PROJ-2")
    assert [(x.issue_key, x.direction) for x in await r.list_links("PROJ-3")] == [("PROJ-2", "outward")]
    rl = await w.create_remote_link("PROJ-2", "https://example.com/x", "Spec")
    assert rl.url == "https://example.com/x"


async def test_attachment_uploads_are_left_to_rest_because_the_v2_tools_read_a_server_side_path():
    jw, cw, _ = parts(JiraIssueWriter, ConfluenceContentWriter)
    for call in (jw.upload_attachment("PROJ-2", b"file body", filename="notes.txt"), cw.upload_attachment("123", b"body", filename="r.txt")):
        with pytest.raises(UnsupportedOperationError):
            await call


async def test_jira_watch_property_and_hierarchy_through_mcp():
    w, r, _ = parts(JiraIssueWriter, JiraIssueReader)
    await w.watch_issue("PROJ-2")
    with pytest.raises(UnsupportedOperationError):                 # watchJiraIssue only acts as the calling user
        await w.watch_issue("PROJ-2", account_id="acc-bob")
    await w.set_property("PROJ-2", "my.app", {"n": 1})
    assert await r.get_property("PROJ-2", "my.app") == {"n": 1}
    await w.convert_hierarchy("PROJ-2", new_issue_type="Epic")
    assert (await r.get_issue("PROJ-2")).issue_type == "Epic"


async def test_jira_deletes_are_not_in_the_v2_catalogue_so_the_router_uses_rest():
    d, r, t = parts(JiraIssueDeleter, JiraIssueReader)
    for call in (d.delete_comment("PROJ-1", "c1"), d.delete_attachment("att1"), d.delete_issue("PROJ-3")):
        with pytest.raises(UnsupportedOperationError):
            await call
    assert (await r.get_issue("PROJ-3")).key == "PROJ-3"                         # nothing was sent
    assert not any(c[0].startswith("delete") for c in t.calls)


async def test_jira_admin_manage_group_through_mcp():
    a, pr, w, _ = parts(JiraAdmin, JiraProjectReader, JiraProjectWriter)
    for call in (a.create_project("NEW", "New project", template="scrum", lead="acc-ann"), a.update_project("NEW", name="Renamed"), a.list_workflows(),
                 a.get_workflow_editor_link("wf1"), a.get_screen("1"), a.update_screen("1", name="Renamed")):
        with pytest.raises(UnsupportedOperationError):                         # project / workflow / screen admin left the v2 catalogue
            await call
    v = await w.manage_version("PROJ", VersionChange(action="create", name="3.0"))
    assert v.name == "3.0" and "3.0" in [x.name for x in await pr.list_versions("PROJ")]


async def test_agile_write_through_mcp():
    w, r, _ = parts(JiraAgileWriter, JiraAgileReader)
    sp = await w.manage_sprint(SprintChange(action="create", board_id=42, name="Sprint 8", goal="Ship"))
    assert sp.state == "future"
    assert (await w.manage_sprint(SprintChange(action="start", sprint_id=sp.id))).state == "active"
    await w.move_issues_to_sprint(sp.id, ["PROJ-2"])
    with pytest.raises(UnsupportedOperationError):                 # manageJiraSprint has no move-to-backlog action
        await w.move_issues_to_backlog(["PROJ-2"])
    await w.rank_issues(["PROJ-2"], before="PROJ-1")
    assert (await w.create_board("Team", filter_id=10)).name == "Team"
    with pytest.raises(InvalidRequestError):
        await w.rank_issues(["PROJ-2"])


async def test_jsm_write_is_partial_comments_and_transitions_only():
    w, _, _ = parts(JsmWriter, JsmReader)
    c = await w.add_request_comment("PROJ-1", "internal note", public=False)
    assert c.public is False
    await w.transition_request("PROJ-1", "31")
    for call in (w.create_request("4", "r1", {}), w.answer_approval("OPS-1", "a1", approve=True), w.add_customers("4", ["a"])):
        with pytest.raises(UnsupportedOperationError):
            await call


async def test_conf_content_write_create_update_move_copy_archive_restore():
    w, r, t = parts(ConfluenceContentWriter, ConfluenceContentReader)
    page = await w.create_content(ContentDraft(title="Fresh", space="9001", body="# Hi", parent_id="100"))
    assert (await r.get_content(page.id)).body_markdown == "# Hi"
    up = await w.update_content("123", ContentUpdate(body="New text", version=3, message="m"))
    assert up.version == 4 and (await r.get_content("123")).body_markdown == "New text"
    with pytest.raises(Exception, match="version"):
        await w.update_content("123", ContentUpdate(body="stale", version=3))
    assert (await w.move_content("125", target_parent_id="100")).parent_id == "100"
    assert (await w.copy_content("123", destination_parent_id="100", title="Copy")).title == "Copy"
    await w.archive_content(["124"])
    assert (await r.get_content("124")).status == "archived"
    await w.unarchive_content(["124"])
    assert (await r.get_content("124")).status == "current"
    assert (await w.restore_version("123", 2)).body_markdown == "# Runbook\n\nRestart nginx"
    await w.set_status("123", "Ready")
    with pytest.raises(InvalidRequestError):
        await w.move_content("125")


async def test_conf_content_write_labels_comments_attachments_tasks_restrictions_watch():
    w, r, _ = parts(ConfluenceContentWriter, ConfluenceContentReader)
    assert {lb.name for lb in await w.add_labels("123", ["alpha"])} >= {"alpha"}
    c = await w.add_comment("123", "Nice")
    inline = await w.add_comment("123", "Typo", inline_anchor={"selectedText": "Restart", "matchIndex": 0, "matchCount": 1})
    assert c.kind == "footer" and inline.kind == "inline"
    assert (await w.update_comment(c.id, "Edited")).body_markdown == "Edited"
    await w.resolve_comment(inline.id)
    assert (await w.set_task_status("t1", complete=True)).status == "complete"
    await w.set_restrictions("124", Restrictions(read_groups=["admins"]))
    assert (await r.get_restrictions("124")).read_groups == ["admins"]
    await w.watch(content_id="123")
    await w.star(content_id="123")
    await w.add_reaction("123", "thumbsup")
    for call in (w.remove_label("123", "alpha"), w.set_property("123", "k", 1), w.delete_content("123"), w.delete_comment("fc1"), w.delete_attachment("cat1")):
        with pytest.raises(UnsupportedOperationError):
            await call


async def test_conf_content_write_spaces_and_permissions_through_mcp():
    sw, pw, _ = parts(ConfluenceSpaceWriter, ConfluencePermissionWriter)
    assert (await sw.create_space("NEW", "New space")).key == "NEW"
    await sw.set_space_instructions("ENG", "Be concise")
    for coro in (pw.add_permissions("123", [{"operation": "read", "group": "eng"}]), pw.remove_permissions("123", []), pw.replace_permissions("123", []),
                 pw.set_public_link("123", enabled=True), pw.set_public_link("123", enabled=False)):
        await coro


async def test_conf_special_infographics_and_maui_apps_left_the_v2_catalogue():
    s, t = parts(ConfluenceSpecialContent)
    for call in (s.create_infographic("123", prompt="Summarize"), s.edit_infographic("123", "i1", prompt="More"), s.create_maui_app("123", spec="counter"),
                 s.get_maui_app("a1"), s.edit_maui_app("a1", spec="reset")):
        with pytest.raises(UnsupportedOperationError):
            await call


async def test_graph_context_add_through_mcp():
    g, t = parts(TeamworkGraphWriter)
    await g.add_relationship("ari:cloud:jira::issue/10001", "jira-work-item-links-jira-work-item", "PROJ-2", title="Related")
    sent = next(c[1] for c in t.calls if c[0] == "addGraphContext")
    assert sent["objectIdentifier"] == "ari:cloud:jira::issue/10001" and sent["targetObjectIdentifier"] == "PROJ-2"
    with pytest.raises(InvalidRequestError):                       # v2 adds one typed relationship, not free-form context
        await g.add_relationship("PROJ-1", "", "PROJ-2")
