"""One test group per operation x MCP cell of design section 11 (layer 4: MCP)."""
import pytest

from atlassian_client.core.errors import NotFoundError, UnsupportedOperationError
from atlassian_client.protocols import (ConfluenceContentReader, ConfluenceSearcher, ConfluenceSpaceReader, ConfluenceTemplateReader,
                                        JiraAgileReader, JiraIssueReader, JiraIssueSearcher, JiraMetadataReader, JiraProjectReader,
                                        JiraUserDirectory, JsmReader, SemanticSearcher, TeamworkGraphReader)
from atlassian_client.protocols.base import implementation_of

from .mcp_helpers import make_mcp, same

pytestmark = [pytest.mark.layer_04, pytest.mark.anyio]


async def impl(proto):
    mcp, transport = make_mcp()
    return implementation_of(mcp, proto), transport


async def test_jira_issue_get_matches_reference():
    issue = await same(JiraIssueReader, "get_issue", "PROJ-1")
    assert issue["key"] == "PROJ-1" and issue["description_markdown"] == "Login fails with **500**"
    reader, t = await impl(JiraIssueReader)
    assert (await reader.get_issue("proj-1")).key == "PROJ-1"
    with pytest.raises(NotFoundError):
        await reader.get_issue("PROJ-404")
    assert t.calls[0][0] == "getAccessibleAtlassianResources" and t.calls[1][0] == "getJiraIssue" and t.calls[1][1]["cloudId"] == "cloud-1"


async def test_jira_issue_search_pages_via_next_page_token_and_rejects_rest_only_options():
    reader, t = await impl(JiraIssueSearcher)
    p1 = await reader.search_issues("project = PROJ", limit=2)
    p2 = await reader.search_issues("project = PROJ", limit=2, cursor=p1.next_cursor)
    assert [len(p1.items), len(p2.items)] == [2, 1] and p2.next_cursor is None and p1.next_cursor
    assert t.calls[-1][1]["nextPageToken"] == "2"
    from atlassian_client.core.pagination import Cursor
    assert Cursor.decode(p1.next_cursor).backend.value == "mcp"
    with pytest.raises(UnsupportedOperationError):
        await reader.search_issues("x", reconcile_issue_ids=[1])
    with pytest.raises(UnsupportedOperationError):
        await reader.count_issues("x")


async def test_jira_comments_list_matches_reference_and_pages():
    await same(JiraIssueReader, "list_comments", "PROJ-1", limit=2)
    reader, _ = await impl(JiraIssueReader)
    first = await reader.list_comments("PROJ-1", limit=2)
    second = await reader.list_comments("PROJ-1", limit=2, cursor=first.next_cursor)
    assert [c.id for c in first.items + second.items] == ["c1", "c2", "c3"]


async def test_jira_changelog_matches_reference():
    got = await same(JiraIssueReader, "list_changelog", "PROJ-1")
    assert got["items"][0]["items"][0]["to_string"] == "In Progress"


async def test_jira_worklogs_matches_reference():
    await same(JiraIssueReader, "list_worklogs", "PROJ-1")


async def test_jira_transitions_list_matches_reference():
    await same(JiraIssueReader, "list_transitions", "PROJ-1")


async def test_jira_attachment_download_decodes_content_and_respects_limits(tmp_path):
    await same(JiraIssueReader, "list_attachments", "PROJ-1")
    reader, _ = await impl(JiraIssueReader)
    got = await reader.download_attachment("att1", extract_text=True)
    assert got.data == b"hello" and got.text == "hello" and got.filename == "log.txt"
    with pytest.raises(Exception, match="max_bytes"):
        await reader.download_attachment("att1", max_bytes=2)
    saved = await reader.download_attachment("att1", dest=tmp_path / "x" / "log.txt")
    assert saved.data is None and (tmp_path / "x" / "log.txt").read_bytes() == b"hello"
    await same(JiraIssueReader, "list_links", "PROJ-1")
    await same(JiraIssueReader, "list_remote_links", "PROJ-1")
    assert await reader.get_property("PROJ-1", "my.app.state") == {"seen": True}
    with pytest.raises(UnsupportedOperationError):
        await reader.list_watchers("PROJ-1")


async def test_jira_projects_matches_reference_and_get_project_by_key():
    await same(JiraProjectReader, "list_projects")
    await same(JiraProjectReader, "list_components", "PROJ")
    await same(JiraProjectReader, "list_versions", "PROJ")
    reader, _ = await impl(JiraProjectReader)
    assert (await reader.get_project("proj")).name == "Project"
    with pytest.raises(NotFoundError):
        await reader.get_project("NOPE")


async def test_jira_fields_meta_supported_subset_and_explicit_gaps():
    await same(JiraMetadataReader, "get_create_fields", "PROJ", "Bug")
    await same(JiraMetadataReader, "list_issue_types", "PROJ")
    await same(JiraMetadataReader, "list_statuses")
    await same(JiraMetadataReader, "list_link_types")
    reader, _ = await impl(JiraMetadataReader)
    for call in (reader.list_fields(), reader.list_priorities(), reader.server_info(), reader.list_issue_types()):
        with pytest.raises(UnsupportedOperationError):
            await call


async def test_jira_filters_dashboards_match_reference():
    await same(JiraMetadataReader, "list_filters")
    await same(JiraMetadataReader, "list_dashboards")


async def test_jira_users_match_reference_and_lookup_rules():
    await same(JiraUserDirectory, "myself")
    await same(JiraUserDirectory, "get_user", "acc-bob")
    await same(JiraUserDirectory, "search_users", "bob")
    reader, _ = await impl(JiraUserDirectory)
    assert await reader.lookup_account_id("ann@example.com") == "acc-ann"
    from atlassian_client.core.errors import AmbiguousReferenceError
    with pytest.raises(AmbiguousReferenceError):
        await reader.lookup_account_id("example.com")
    assert len(await reader.assignable_users(project="PROJ")) == 2


async def test_agile_read_supported_subset_and_gaps():
    await same(JiraAgileReader, "list_boards")
    await same(JiraAgileReader, "get_board_config", 42)
    await same(JiraAgileReader, "list_sprints", 42)
    await same(JiraAgileReader, "list_sprint_issues", 7)
    await same(JiraAgileReader, "list_board_issues", 42, limit=1)
    reader, _ = await impl(JiraAgileReader)
    for call in (reader.get_board(42), reader.get_sprint(7), reader.list_backlog(42), reader.list_epics(42)):
        with pytest.raises(UnsupportedOperationError):
            await call


async def test_jsm_read_is_partial_only_get_request_works():
    reader, _ = await impl(JsmReader)
    req = await reader.get_request("PROJ-1")
    assert req.issue_key == "PROJ-1" and req.summary == "Login fails"
    for call in (reader.info(), reader.list_service_desks(), reader.get_request_sla("OPS-1"), reader.search_knowledge_base("vpn")):
        with pytest.raises(UnsupportedOperationError):
            await call


async def test_conf_content_get_matches_reference_formats_and_missing():
    page = await same(ConfluenceContentReader, "get_content", "123")
    assert page["body_markdown"] == "# Runbook\n\nRestart **nginx**"
    reader, t = await impl(ConfluenceContentReader)
    assert (await reader.get_content("123", body_format="none")).body_markdown is None
    assert "bodyFormat" not in t.calls[-1][1]
    with pytest.raises(NotFoundError):
        await reader.get_content("999999")
    await same(ConfluenceContentReader, "list_content", limit=10)


async def test_conf_cql_search_pages_with_cursor_and_rejects_empty():
    reader, _ = await impl(ConfluenceSearcher)
    p1 = await reader.search_cql('space = "ENG"', limit=2)
    p2 = await reader.search_cql('space = "ENG"', limit=2, cursor=p1.next_cursor)
    assert [h.content_id for h in p1.items + p2.items] == ["123", "124", "125"] and p2.next_cursor is None
    from atlassian_client.core.errors import InvalidRequestError
    with pytest.raises(InvalidRequestError):
        await reader.search_cql("  ")


async def test_conf_tree_is_partial_children_only():
    reader, _ = await impl(ConfluenceContentReader)
    kids = await reader.list_children("123", limit=1)
    nxt = await reader.list_children("123", limit=1, cursor=kids.next_cursor)
    assert [c.id for c in kids.items + nxt.items] == ["124", "125"]
    for call in (reader.list_ancestors("123"), reader.list_descendants("123")):
        with pytest.raises(UnsupportedOperationError):
            await call


async def test_conf_versions_diff_matches_reference():
    await same(ConfluenceContentReader, "list_versions", "123")
    diff = await same(ConfluenceContentReader, "diff_versions", "123", 2, 3)
    assert "-Restart nginx" in diff["unified"] and "+Restart **nginx**" in diff["unified"]


async def test_conf_comments_attachments_labels_match_reference(tmp_path):
    reader, _ = await impl(ConfluenceContentReader)
    comments = (await reader.list_comments("123")).items
    assert {(c.kind, c.body_markdown) for c in comments} == {("footer", "Thanks"), ("inline", "Typo here")}
    await same(ConfluenceContentReader, "list_attachments", "123")
    got = await reader.download_attachment("cat2", extract_text=True)
    assert got.data == b"notes" and got.text == "notes"
    assert [lb.name for lb in await reader.list_labels("123")] == ["runbook"]
    with pytest.raises(UnsupportedOperationError):
        await reader.get_properties("123")


async def test_conf_permissions_match_reference():
    await same(ConfluenceContentReader, "get_permissions", "123")
    res = await same(ConfluenceContentReader, "get_restrictions", "123")
    assert res["read_groups"] == ["eng"]


async def test_conf_tasks_templates_reactions_match_reference():
    await same(ConfluenceContentReader, "list_tasks", content_id="123")
    await same(ConfluenceTemplateReader, "list_templates")
    await same(ConfluenceTemplateReader, "get_template", "tpl1")
    spaces = await same(ConfluenceSpaceReader, "list_spaces")
    assert [s["key"] for s in spaces["items"]] == ["ENG", "OPS"]
    await same(ConfluenceSpaceReader, "get_space", "ENG")
    reader, _ = await impl(ConfluenceSpaceReader)
    assert await reader.get_space_instructions("ENG") == "Be concise."


async def test_conf_macros_resolve_returns_rendered_markdown():
    reader, _ = await impl(ConfluenceContentReader)
    page = await reader.resolve_macros("123")
    assert page.id == "123" and "Restart" in (page.body_markdown or "")


async def test_graph_context_and_objects():
    reader, _ = await impl(TeamworkGraphReader)
    ctx = await reader.get_context("https://example.atlassian.net/browse/PROJ-1", max_items=5)
    assert ctx.related[0].title == "Login fails" and ctx.relationships == [{"type": "mentions"}]
    objs = await reader.get_objects(["ari:a", "ari:b"])
    assert [o.ari for o in objs] == ["ari:a", "ari:b"]
    writer = implementation_of(make_mcp()[0], __import__("atlassian_client.protocols", fromlist=["TeamworkGraphWriter"]).TeamworkGraphWriter)
    assert await writer.add_context("x", {"a": 1}) is None


async def test_graph_semantic_search_hits():
    reader, t = await impl(SemanticSearcher)
    hits = await reader.semantic_search("rotate keys", products=["confluence"], limit=5)
    assert hits[0].title == "Runbook" and hits[0].score == 0.9
    assert t.calls[-1][1]["products"] == ["confluence"]
