"""Per-row smoke scenarios for the Data Center REST backend (design 11 x rest_dc)."""
from __future__ import annotations

import pytest

from atlian_assiant.core.errors import (
    ConflictError,
    DeploymentMismatchError,
    NotFoundError,
    UnsupportedOperationError,
)
from atlian_assiant.models.confluence import ContentDraft, ContentUpdate
from atlian_assiant.models.jira import IssueDraft, IssueUpdate, SprintChange
from atlian_assiant.protocols import (
    AutomationTrigger,
    ConfluenceContentDeleter,
    ConfluenceContentReader,
    ConfluenceContentWriter,
    ConfluenceSearcher,
    ConfluenceSpaceReader,
    ConfluenceTemplateReader,
    JiraAdmin,
    JiraAgileReader,
    JiraAgileWriter,
    JiraIssueDeleter,
    JiraIssueReader,
    JiraIssueSearcher,
    JiraIssueWriter,
    JiraJqlTools,
    JiraMetadataReader,
    JiraProjectReader,
    JiraUserDirectory,
    JsmReader,
    JsmWriter,
    WebhookManager,
)

from .matrix_harness import Ctx, Scenario

ROWS: dict[str, Scenario] = {}


def row(slug: str):
    def deco(fn: Scenario) -> Scenario:
        ROWS[slug] = fn
        return fn
    return deco


def last(ctx: Ctx, method: str, suffix: str) -> dict:
    return next(r for r in reversed(ctx.server.dc_requests) if r["method"] == method and r["path"].endswith(suffix))


@row("jira-issue-get")
async def _(ctx: Ctx) -> None:
    issue = await ctx.impl(JiraIssueReader).get_issue("PROJ-1")
    assert issue.key == "PROJ-1" and issue.description_markdown == "Login fails with **500**"      # wiki -> Markdown
    assert issue.assignee and issue.assignee.username == "ann" and issue.assignee.account_id is None
    with pytest.raises(NotFoundError):
        await ctx.impl(JiraIssueReader).get_issue("PROJ-999")
    assert all(r["path"].startswith("/rest/api/2/") for r in ctx.server.dc_requests)


@row("jira-issue-search")
async def _(ctx: Ctx) -> None:
    s = ctx.impl(JiraIssueSearcher)
    p1 = await s.search_issues("project = PROJ", limit=2)
    p2 = await s.search_issues("project = PROJ", limit=2, cursor=p1.next_cursor)
    assert [len(p1.items), len(p2.items)] == [2, 1] and p2.next_cursor is None
    body = last(ctx, "POST", "/search")["json"]
    assert body["jql"] == "project = PROJ" and body["startAt"] == 2
    assert await s.count_issues("project = PROJ") == 3


@row("jira-comments-list")
async def _(ctx: Ctx) -> None:
    page = await ctx.impl(JiraIssueReader).list_comments("PROJ-1", limit=2)
    assert [c.id for c in page.items] == ["c1", "c2"] and page.next_cursor
    assert page.items[0].body_markdown == "Login fails with **500**"


@row("jira-changelog")
async def _(ctx: Ctx) -> None:
    item = (await ctx.impl(JiraIssueReader).list_changelog("PROJ-1")).items[0].items[0]
    assert (item.field, item.to_string) == ("status", "In Progress")


@row("jira-worklogs")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JiraIssueReader).list_worklogs("PROJ-1")).items[0].time_spent_seconds == 7200


@row("jira-transitions-list")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JiraIssueReader).list_transitions("PROJ-1"))[0].name == "Done"


@row("jira-attachment-download")
async def _(ctx: Ctx) -> None:
    r = ctx.impl(JiraIssueReader)
    assert (await r.list_attachments("PROJ-1"))[0].id == "att1"
    got = await r.download_attachment("att1")
    assert got.data == b"hello"
    assert any(x["path"].startswith("/secure/attachment/att1/") for x in ctx.server.dc_requests)


@row("jira-issue-create-edit")
async def _(ctx: Ctx) -> None:
    w = ctx.impl(JiraIssueWriter)
    created = await w.create_issue(IssueDraft(project="PROJ", issue_type="Task", summary="New", description="**bold** text", assignee="ann"))
    assert created.key
    body = last(ctx, "POST", "/issue")["json"]
    assert body["fields"]["description"] == "*bold* text" and body["fields"]["assignee"] == {"name": "ann"}
    await w.edit_issue("PROJ-1", IssueUpdate(summary="Changed", description="# Head"))
    assert last(ctx, "PUT", "/issue/PROJ-1")["json"]["fields"]["description"] == "h1. Head"
    await w.assign_issue("PROJ-1", "ann")
    assert last(ctx, "PUT", "/issue/PROJ-1/assignee")["json"] == {"name": "ann"}


@row("jira-bulk-create")
async def _(ctx: Ctx) -> None:
    out = await ctx.impl(JiraIssueWriter).bulk_create_issues([IssueDraft(project="PROJ", issue_type="Task", summary="A"), IssueDraft(project="PROJ", issue_type="Task", summary="B")])
    assert len(out) == 2


@row("jira-transition")
async def _(ctx: Ctx) -> None:
    await ctx.impl(JiraIssueWriter).transition_issue("PROJ-1", "Done", comment="done")
    assert last(ctx, "POST", "/issue/PROJ-1/transitions")["json"]["transition"]


@row("jira-comment-worklog-add")
async def _(ctx: Ctx) -> None:
    w = ctx.impl(JiraIssueWriter)
    c = await w.add_comment("PROJ-1", "hello *there*")
    assert c.id and isinstance(last(ctx, "POST", "/issue/PROJ-1/comment")["json"]["body"], str)
    await w.edit_comment("PROJ-1", c.id, "edited")
    await w.add_worklog("PROJ-1", "1h")


@row("jira-links-write")
async def _(ctx: Ctx) -> None:
    w = ctx.impl(JiraIssueWriter)
    await w.create_link("Blocks", "PROJ-1", "PROJ-2")
    await w.create_remote_link("PROJ-1", "https://example.com", "Example")


@row("jira-attachment-upload")
async def _(ctx: Ctx) -> None:
    out = await ctx.impl(JiraIssueWriter).upload_attachment("PROJ-1", b"data", filename="a.txt")
    assert out and last(ctx, "POST", "/issue/PROJ-1/attachments")["headers"].get("x-atlassian-token") == "no-check"


@row("jira-delete")
async def _(ctx: Ctx) -> None:
    d = ctx.impl(JiraIssueDeleter)
    await d.delete_comment("PROJ-1", "c1")
    await d.delete_issue("PROJ-1")
    await d.delete_attachment("att1")


@row("jira-projects")
async def _(ctx: Ctx) -> None:
    r = ctx.impl(JiraProjectReader)
    page = await r.list_projects()
    assert page.items[0].key == "PROJ"
    assert (await r.get_project("PROJ")).key == "PROJ"
    await r.list_components("PROJ")
    await r.list_versions("PROJ")


@row("jira-fields-meta")
async def _(ctx: Ctx) -> None:
    m = ctx.impl(JiraMetadataReader)
    assert await m.list_fields()
    assert await m.list_issue_types()
    assert await m.list_priorities()
    assert await m.list_link_types()
    assert await m.list_statuses()
    assert (await m.server_info()).deployment_type
    assert any(f.id for f in await m.get_create_fields("PROJ", "Bug"))


@row("jira-users")
async def _(ctx: Ctx) -> None:
    u = ctx.impl(JiraUserDirectory)
    assert (await u.search_users("ann"))[0].username == "ann"
    assert (await u.get_user("ann")).display_name == "Ann Lee"
    assert await u.lookup_account_id("ann") == "ann"
    assert (await u.myself()).username
    assert await u.assignable_users(project="PROJ")


@row("jira-filters-dashboards")
async def _(ctx: Ctx) -> None:
    m = ctx.impl(JiraMetadataReader)
    assert (await m.list_filters()).items
    assert (await m.list_dashboards()).items


@row("jira-jql-tools")
async def _(ctx: Ctx) -> None:
    t = ctx.impl(JiraJqlTools)
    assert (await t.autocomplete())["visibleFieldNames"]
    with pytest.raises(UnsupportedOperationError):
        await t.parse_jql(["project = PROJ"])
    with pytest.raises(UnsupportedOperationError):
        await t.sanitize_jql(["x"])
    with pytest.raises(UnsupportedOperationError):
        await t.match_jql([1], ["x"])
    with pytest.raises(DeploymentMismatchError):
        await t.evaluate_expression("1", {})


@row("jira-admin")
async def _(ctx: Ctx) -> None:
    a = ctx.impl(JiraAdmin)
    assert await a.list_workflows()
    with pytest.raises(UnsupportedOperationError):
        await a.get_screen("1")


@row("agile-read")
async def _(ctx: Ctx) -> None:
    r = ctx.impl(JiraAgileReader)
    board = (await r.list_boards()).items[0]
    assert (board.id, board.type) == (42, "scrum")
    assert [c["name"] for c in (await r.get_board_config(42)).columns] == ["To Do", "Done"]
    assert (await r.list_sprints(42)).items[0].state == "active"
    assert [i.key for i in (await r.list_sprint_issues(7)).items] == ["PROJ-1"]
    assert (await r.list_epics(42)).items
    assert (await r.list_backlog(42)).items
    assert (await r.list_board_issues(42)).items
    assert (await r.get_sprint(7)).name == "Sprint 7" and (await r.get_board(42)).name
    assert any(x["path"].startswith("/rest/agile/1.0/") for x in ctx.server.dc_requests)
    with pytest.raises(NotFoundError):
        await r.get_board(999)


@row("agile-write")
async def _(ctx: Ctx) -> None:
    w = ctx.impl(JiraAgileWriter)
    sp = await w.manage_sprint(SprintChange(action="create", board_id=42, name="Sprint 8"))
    assert (await w.manage_sprint(SprintChange(action="start", sprint_id=sp.id))).state == "active"
    await w.move_issues_to_sprint(7, ["PROJ-2"])
    await w.rank_issues(["PROJ-2"], before="PROJ-1")
    assert (await w.create_board("Team", filter_id=10)).name == "Team"
    assert any(x["path"] == "/rest/agile/1.0/sprint" for x in ctx.server.dc_requests)


@row("jsm-read")
async def _(ctx: Ctx) -> None:
    r = ctx.impl(JsmReader)
    assert (await r.info()).version
    assert (await r.list_service_desks()).items[0].project_key == "OPS"
    assert (await r.list_request_types("4")).items
    assert (await r.list_requests(service_desk_id="4")).items[0].issue_key == "OPS-1"
    assert (await r.get_request("OPS-1")).description == "Cannot connect"
    assert len((await r.list_request_comments("OPS-1")).items) == 2
    assert (await r.get_request_sla("OPS-1"))[0].name
    assert (await r.search_knowledge_base("vpn"))[0].title == "Fix VPN"
    assert (await r.list_organizations()).items and (await r.list_customers("4")).items


@row("jsm-write")
async def _(ctx: Ctx) -> None:
    w = ctx.impl(JsmWriter)
    req = await w.create_request("4", "r1", {"summary": "Printer", "description": "Jammed"})
    assert req.issue_key.startswith("OPS-")
    assert (await w.add_request_comment("OPS-1", "ok", public=False)).public is False
    await w.transition_request("OPS-1", "711")
    await w.answer_approval("OPS-1", "a1", approve=True)
    await w.add_customers("4", ["bob"])


@row("conf-content-get")
async def _(ctx: Ctx) -> None:
    r, s = ctx.impl(ConfluenceContentReader), ctx.impl(ConfluenceSpaceReader)
    page = await r.get_content("123")
    assert (page.id, page.title, page.version) == ("123", "Runbook", 3)
    assert page.body_markdown == "# Runbook\n\nRestart **nginx**"
    assert (await r.get_content("123", body_format="none")).body_markdown is None
    with pytest.raises(NotFoundError):
        await r.get_content("999999")
    assert len((await r.list_content(limit=2)).items) == 2
    assert (await s.get_space("ENG")).key == "ENG"
    assert (await s.list_spaces()).items
    assert all(x["path"].startswith("/rest/api/") for x in ctx.server.dc_requests if x["path"].startswith(("/rest", "/wiki")))
    assert not any("/wiki/api/v2" in x["path"] for x in ctx.server.dc_requests)


@row("conf-cql-search")
async def _(ctx: Ctx) -> None:
    s = ctx.impl(ConfluenceSearcher)
    assert (await s.search_cql("type = page", limit=1)).items
    assert last(ctx, "GET", "/search")["query"]["cql"] == "type = page"
    from atlian_assiant.core.errors import InvalidRequestError
    with pytest.raises(InvalidRequestError):
        await s.search_cql("  ")


@row("conf-tree")
async def _(ctx: Ctx) -> None:
    r = ctx.impl(ConfluenceContentReader)
    assert [c.id for c in (await r.list_children("123")).items] == ["124", "125"]
    assert [a.id for a in await r.list_ancestors("123")] == ["100"]
    assert {d.id for d in (await r.list_descendants("123")).items} == {"124", "125"}


@row("conf-versions-diff")
async def _(ctx: Ctx) -> None:
    r = ctx.impl(ConfluenceContentReader)
    assert [v.number for v in (await r.list_versions("123")).items] == [3, 2]
    diff = await r.diff_versions("123", 2, 3)
    assert "+Restart **nginx**" in diff.unified


@row("conf-comments-attachments-labels")
async def _(ctx: Ctx) -> None:
    r = ctx.impl(ConfluenceContentReader)
    assert {c.kind for c in (await r.list_comments("123")).items} == {"footer", "inline"}
    assert [a.title for a in (await r.list_attachments("123")).items] == ["diagram.png", "notes.txt"]
    assert (await r.download_attachment("cat2")).data == b"notes"
    assert [lb.name for lb in await r.list_labels("123")] == ["runbook"]
    assert await r.get_properties("123") == {"app.state": {"ok": True}}


@row("conf-permissions")
async def _(ctx: Ctx) -> None:
    r, s = ctx.impl(ConfluenceContentReader), ctx.impl(ConfluenceSpaceReader)
    assert (await r.get_permissions("123")).operations is not None
    res = await r.get_restrictions("123")
    assert res.restricted and res.read_groups == ["eng"]
    assert (await s.list_space_permissions("ENG")).items is not None


@row("conf-tasks-templates-reactions")
async def _(ctx: Ctx) -> None:
    t = ctx.impl(ConfluenceTemplateReader)
    assert await t.list_templates()
    assert (await t.get_template((await t.list_templates())[0].id)).id
    with pytest.raises(UnsupportedOperationError):
        await ctx.impl(ConfluenceContentReader).list_tasks(content_id="123")


@row("conf-content-write")
async def _(ctx: Ctx) -> None:
    w, r = ctx.impl(ConfluenceContentWriter), ctx.impl(ConfluenceContentReader)
    made = await w.create_content(ContentDraft(title="New page", space="ENG", body="# Hi", parent_id="100"))
    assert made.title == "New page" and last(ctx, "POST", "/content")["json"]["body"]["storage"]["representation"] == "storage"
    upd = await w.update_content("123", ContentUpdate(title="Renamed", version=3))
    assert upd.version == 4 and last(ctx, "PUT", "/content/123")["json"]["version"]["number"] == 4
    with pytest.raises(ConflictError):
        await w.update_content("123", ContentUpdate(title="Again", version=1))
    assert (await w.copy_content("123", destination_parent_id="100", title="Copy")).title == "Copy"
    await w.move_content("125", target_parent_id="100")
    assert {lb.name for lb in await w.add_labels("123", ["alpha"])} >= {"alpha"}
    await w.remove_label("123", "alpha")
    assert (await w.add_comment("123", "Nice")).kind == "footer"
    await w.set_property("123", "app.new", {"v": 1})
    with pytest.raises(UnsupportedOperationError):
        await w.archive_content(["124"])
    assert (await r.get_content("123")).title == "Renamed"


@row("conf-content-delete")
async def _(ctx: Ctx) -> None:
    d, r = ctx.impl(ConfluenceContentDeleter), ctx.impl(ConfluenceContentReader)
    await d.delete_content("125")
    with pytest.raises(NotFoundError):
        await r.get_content("125")
    await d.delete_comment("fc1")
    await d.delete_attachment("cat1")


@row("conf-macros")
async def _(ctx: Ctx) -> None:
    page = await ctx.impl(ConfluenceContentReader).resolve_macros("123")
    assert page.id == "123"
    assert "body.view" in last(ctx, "GET", "/content/123")["query"]["expand"]


@row("webhook-management")
async def _(ctx: Ctx) -> None:
    from atlian_assiant.core.errors import DeploymentMismatchError
    from atlian_assiant.protocols import AdminWebhookManager
    a, w = ctx.impl(AdminWebhookManager), ctx.impl(WebhookManager)
    made = await a.register_admin_webhook("hook", "https://hooks.corp.example/jira", ["jira:issue_created"], jql="project = PROJ", secret="s3cret-value")
    assert made.id and last(ctx, "POST", "/rest/webhooks/1.0/webhook".replace("/rest/webhooks/1.0", ""))["path"].startswith("/rest/webhooks/1.0")
    assert [x.id for x in await w.list_webhooks()] == [made.id]
    await a.update_admin_webhook(made.id, "hook2", "https://hooks.corp.example/jira", ["jira:issue_updated"])
    await a.delete_admin_webhook(made.id)
    with pytest.raises(DeploymentMismatchError):
        await w.refresh(["1"])
    cw = ctx.backend.webhooks
    reg = await cw.register_confluence_webhook("c-hook", "https://hooks.corp.example/conf", ["page_created"])
    assert [x.id for x in await cw.list_confluence_webhooks()] == [reg.id]
    await cw.update_confluence_webhook(reg.id, "c-hook", "https://hooks.corp.example/conf", ["page_updated"])
    await cw.test_confluence_webhook("https://hooks.corp.example/conf")
    assert (await cw.confluence_webhook_statistics(reg.id))["success"] == 3
    await cw.delete_confluence_webhook(reg.id)
    assert last(ctx, "POST", "/webhooks")["headers"]["authorization"].startswith("Bearer ")


@row("automation-trigger")
async def _(ctx: Ctx) -> None:
    from atlian_assiant.core.errors import ConfigurationError
    await ctx.impl(AutomationTrigger).trigger_incoming_webhook("https://jira.corp.example/rest/cb-automation/latest/hooks/abc", issue_keys=["PROJ-1"])
    assert "authorization" not in ctx.server.dc_requests[-1]["headers"]               # the URL is the capability: no credentials are sent
    with pytest.raises(ConfigurationError):
        await ctx.impl(AutomationTrigger).trigger_incoming_webhook("https://evil.example/hook")


@row("long-tail")
async def _(ctx: Ctx) -> None:
    from atlian_assiant.protocols import RawRequester  # noqa: F401


@row("export-jira-csv")
async def _(ctx: Ctx, tmp_path=None) -> None:
    import tempfile
    from pathlib import Path

    from atlian_assiant.protocols import JiraExporter
    dest = Path(tempfile.mkdtemp()) / "out.csv"
    await ctx.impl(JiraExporter).export_issues_csv("project = PROJ", dest=dest)
    lines = dest.read_text().splitlines()
    assert lines[0].startswith("key,summary") and len(lines) == 4 and lines[1].startswith("PROJ-1,Login fails,In Progress")


@row("export-conf-page")
async def _(ctx: Ctx) -> None:
    import tempfile
    from pathlib import Path

    from atlian_assiant.protocols import ConfluenceExporter
    e, d = ctx.impl(ConfluenceExporter), Path(tempfile.mkdtemp())
    assert "Restart **nginx**" in (await e.export_content("123", format="markdown", dest=d / "p.md")).read_text()
    assert "<h1>Runbook</h1>" in (await e.export_content("123", format="html", dest=d / "p.html")).read_text()
    with pytest.raises(UnsupportedOperationError) as exc:
        await e.export_content("123", format="pdf", dest=d / "p.pdf")
    assert "browser" in exc.value.hint


@row("export-conf-space")
async def _(ctx: Ctx) -> None:
    import tempfile
    from pathlib import Path

    from atlian_assiant.protocols import ConfluenceExporter
    e, d = ctx.impl(ConfluenceExporter), Path(tempfile.mkdtemp())
    index = (await e.export_space("ENG", format="csv", dest=d / "s.csv")).read_text().splitlines()
    assert index[0] == "id,title,parent_id,version,url" and len(index) > 1
    assert "<section" in (await e.export_space("ENG", format="html", dest=d / "s.html")).read_text()
    with pytest.raises(UnsupportedOperationError):
        await e.export_space("ENG", format="xml", dest=d / "s.xml")
