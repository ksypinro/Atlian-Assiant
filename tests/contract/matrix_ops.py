"""One check per canonical operation, written against the shared scenario dataset (design 11, 18).

The same checks run against the Cloud REST backend (the reference, so a wrong check fails there first) and against every backend that declares the
operation. A backend therefore proves a matrix cell by returning exactly what REST returns for the same data.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import mkdtemp

import pytest

from atlian_assiant.core.errors import NotFoundError, PolicyDeniedError
from atlian_assiant.models.confluence import ContentDraft, ContentUpdate
from atlian_assiant.models.jira import IssueDraft, IssueUpdate, SprintChange
from atlian_assiant.protocols import (
    AnalyticsReader,
    ConfluenceContentDeleter,
    ConfluenceContentReader,
    ConfluenceContentWriter,
    ConfluenceExporter,
    ConfluenceSearcher,
    ConfluenceSpaceReader,
    ConfluenceTemplateReader,
    GraphQLExecutor,
    JiraAdmin,
    JiraAgileReader,
    JiraAgileWriter,
    JiraExporter,
    JiraIssueDeleter,
    JiraIssueReader,
    JiraIssueSearcher,
    JiraIssueWriter,
    JiraMetadataReader,
    JiraProjectReader,
    JiraUserDirectory,
    JsmReader,
    JsmWriter,
    SemanticSearcher,
    TeamworkGraphReader,
)

from .matrix_harness import Ctx

ACCOUNT = "5b10a2844c20165700ede21a"          # a 24-hex Atlassian accountId: resolved without a directory lookup
Check = Callable[[Ctx], Awaitable[None]]
OP_CHECKS: dict[str, Check] = {}


def op(*ids: str) -> Callable[[Check], Check]:
    def deco(fn: Check) -> Check:
        for i in ids:
            OP_CHECKS[i] = fn
        return fn
    return deco


def sent(ctx: Ctx, method: str, suffix: str) -> bool:
    return any(r["method"] == method and r["path"].endswith(suffix) for r in ctx.server.requests)


# ------------------------------------------------------------------------------------------------------------------- Jira read
@op("jira.issue.get")
async def _(ctx: Ctx) -> None:
    issue = await ctx.impl(JiraIssueReader).get_issue("PROJ-1")
    assert (issue.key, issue.summary) == ("PROJ-1", "Login fails")
    assert issue.status and issue.status.name == "In Progress" and issue.assignee and issue.assignee.display_name == "Ann Lee"
    with pytest.raises(NotFoundError):
        await ctx.impl(JiraIssueReader).get_issue("PROJ-999")


@op("jira.issue.search")
async def _(ctx: Ctx) -> None:
    s = ctx.impl(JiraIssueSearcher)
    p1 = await s.search_issues("project = PROJ", limit=2)
    p2 = await s.search_issues("project = PROJ", limit=2, cursor=p1.next_cursor)
    assert [len(p1.items), len(p2.items)] == [2, 1] and p1.next_cursor and p2.next_cursor is None


@op("jira.issue.count")
async def _(ctx: Ctx) -> None:
    assert await ctx.impl(JiraIssueSearcher).count_issues("project = PROJ") == 3


@op("jira.issue.comments.list")
async def _(ctx: Ctx) -> None:
    r = ctx.impl(JiraIssueReader)
    first = await r.list_comments("PROJ-1", limit=2)
    second = await r.list_comments("PROJ-1", limit=2, cursor=first.next_cursor)
    assert [c.id for c in first.items + second.items] == ["c1", "c2", "c3"]


@op("jira.issue.changelog.list")
async def _(ctx: Ctx) -> None:
    item = (await ctx.impl(JiraIssueReader).list_changelog("PROJ-1")).items[0].items[0]
    assert (item.field, item.to_string) == ("status", "In Progress")


@op("jira.issue.worklogs.list")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JiraIssueReader).list_worklogs("PROJ-1")).items[0].time_spent_seconds == 7200


@op("jira.issue.transitions.list")
async def _(ctx: Ctx) -> None:
    ts = await ctx.impl(JiraIssueReader).list_transitions("PROJ-1")
    assert ts[0].name == "Done" and ts[0].to_status and ts[0].to_status.name == "Done"


@op("jira.issue.attachments.list")
async def _(ctx: Ctx) -> None:
    assert [a.id for a in await ctx.impl(JiraIssueReader).list_attachments("PROJ-1")] == ["att1"]


@op("jira.issue.attachment.download")
async def _(ctx: Ctx) -> None:
    got = await ctx.impl(JiraIssueReader).download_attachment("att1")
    assert got.data == b"hello" and got.filename == "log.txt"


# ------------------------------------------------------------------------------------------------------------------- Jira write
@op("jira.issue.create")
async def _(ctx: Ctx) -> None:
    created = await ctx.impl(JiraIssueWriter).create_issue(IssueDraft(project="PROJ", issue_type="Task", summary="Brand new", description="Plain text"))
    assert created.key.startswith("PROJ-") and sent(ctx, "POST", "/issue")


@op("jira.issue.bulk_create")
async def _(ctx: Ctx) -> None:
    out = await ctx.impl(JiraIssueWriter).bulk_create_issues([IssueDraft(project="PROJ", issue_type="Task", summary="A"), IssueDraft(project="PROJ", issue_type="Task", summary="B")])
    assert len(out) == 2 and sent(ctx, "POST", "/issue/bulk")


@op("jira.issue.edit")
async def _(ctx: Ctx) -> None:
    await ctx.impl(JiraIssueWriter).edit_issue("PROJ-1", IssueUpdate(summary="Changed"))
    assert sent(ctx, "PUT", "/issue/PROJ-1")


@op("jira.issue.assign")
async def _(ctx: Ctx) -> None:
    await ctx.impl(JiraIssueWriter).assign_issue("PROJ-2", ACCOUNT)
    assert sent(ctx, "PUT", "/issue/PROJ-2/assignee")


@op("jira.issue.transition")
async def _(ctx: Ctx) -> None:
    await ctx.impl(JiraIssueWriter).transition_issue("PROJ-1", "Done")
    assert sent(ctx, "POST", "/issue/PROJ-1/transitions")


@op("jira.issue.comment.add")
async def _(ctx: Ctx) -> None:
    c = await ctx.impl(JiraIssueWriter).add_comment("PROJ-1", "hello there")
    assert c.id and sent(ctx, "POST", "/issue/PROJ-1/comment")


@op("jira.issue.comment.edit")
async def _(ctx: Ctx) -> None:
    await ctx.impl(JiraIssueWriter).edit_comment("PROJ-1", "c1", "edited")
    assert sent(ctx, "PUT", "/issue/PROJ-1/comment/c1")


@op("jira.issue.worklog.add")
async def _(ctx: Ctx) -> None:
    await ctx.impl(JiraIssueWriter).add_worklog("PROJ-1", "1h")
    assert sent(ctx, "POST", "/issue/PROJ-1/worklog")


@op("jira.issue.link.create")
async def _(ctx: Ctx) -> None:
    await ctx.impl(JiraIssueWriter).create_link("Blocks", "PROJ-1", "PROJ-2")
    assert sent(ctx, "POST", "/issueLink")


@op("jira.issue.remote_link.create")
async def _(ctx: Ctx) -> None:
    await ctx.impl(JiraIssueWriter).create_remote_link("PROJ-1", "https://example.com/x", "Example")
    assert sent(ctx, "POST", "/issue/PROJ-1/remotelink")


@op("jira.issue.attachment.upload")
async def _(ctx: Ctx) -> None:
    out = await ctx.impl(JiraIssueWriter).upload_attachment("PROJ-1", b"report body", filename="report.txt")
    assert out and out[0].filename == "report.txt" and sent(ctx, "POST", "/issue/PROJ-1/attachments")


@op("jira.issue.delete")
async def _(ctx: Ctx) -> None:
    await ctx.impl(JiraIssueDeleter).delete_issue("PROJ-3")
    assert sent(ctx, "DELETE", "/issue/PROJ-3")


@op("jira.issue.comment.delete")
async def _(ctx: Ctx) -> None:
    await ctx.impl(JiraIssueDeleter).delete_comment("PROJ-1", "c1")
    assert sent(ctx, "DELETE", "/issue/PROJ-1/comment/c1")


@op("jira.issue.attachment.delete")
async def _(ctx: Ctx) -> None:
    await ctx.impl(JiraIssueDeleter).delete_attachment("att1")
    assert sent(ctx, "DELETE", "/attachment/att1")


# ------------------------------------------------------------------------------------------------------------------- projects / metadata / users
@op("jira.project.list")
async def _(ctx: Ctx) -> None:
    assert [p.key for p in (await ctx.impl(JiraProjectReader).list_projects()).items] == ["PROJ", "OPS"]


@op("jira.project.get")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JiraProjectReader).get_project("PROJ")).key == "PROJ"


@op("jira.project.components.list")
async def _(ctx: Ctx) -> None:
    assert [c.name for c in await ctx.impl(JiraProjectReader).list_components("PROJ")] == ["API"]


@op("jira.project.versions.list")
async def _(ctx: Ctx) -> None:
    assert [v.name for v in await ctx.impl(JiraProjectReader).list_versions("PROJ")] == ["1.0"]


@op("jira.project.create")
async def _(ctx: Ctx) -> None:
    p = await ctx.impl(JiraAdmin).create_project("NEW", "New project", template="scrum", lead=ACCOUNT)
    assert p.key == "NEW" and sent(ctx, "POST", "/project")


@op("jira.project.update")
async def _(ctx: Ctx) -> None:
    await ctx.impl(JiraAdmin).update_project("PROJ", name="Renamed")
    assert sent(ctx, "PUT", "/project/PROJ")


@op("jira.meta.fields.list")
async def _(ctx: Ctx) -> None:
    assert {f.id for f in await ctx.impl(JiraMetadataReader).list_fields()} == {"summary", "customfield_10016"}


@op("jira.meta.issue_types.list")
async def _(ctx: Ctx) -> None:
    assert [t.name for t in await ctx.impl(JiraMetadataReader).list_issue_types()] == ["Bug", "Task"]


@op("jira.meta.create_fields")
async def _(ctx: Ctx) -> None:
    assert [(f.id, f.required) for f in await ctx.impl(JiraMetadataReader).get_create_fields("PROJ", "Bug")] == [("summary", True), ("customfield_10016", False)]


@op("jira.meta.statuses.list")
async def _(ctx: Ctx) -> None:
    assert [s.name for s in await ctx.impl(JiraMetadataReader).list_statuses()] == ["To Do", "Done"]


@op("jira.meta.priorities.list")
async def _(ctx: Ctx) -> None:
    assert await ctx.impl(JiraMetadataReader).list_priorities() == ["High", "Low"]


@op("jira.meta.link_types.list")
async def _(ctx: Ctx) -> None:
    assert await ctx.impl(JiraMetadataReader).list_link_types() == ["Blocks", "Relates"]


@op("jira.meta.server_info")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JiraMetadataReader).server_info()).deployment_type == "Cloud"


@op("jira.user.myself")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JiraUserDirectory).myself()).account_id == "acc-ann"


@op("jira.user.get")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JiraUserDirectory).get_user("acc-bob")).display_name == "Bob Ray"


@op("jira.user.search")
async def _(ctx: Ctx) -> None:
    assert [u.display_name for u in await ctx.impl(JiraUserDirectory).search_users("lee")] == ["Ann Lee"]


@op("jira.user.lookup_account_id")
async def _(ctx: Ctx) -> None:
    assert await ctx.impl(JiraUserDirectory).lookup_account_id("ann@example.com") == "acc-ann"


@op("jira.user.assignable")
async def _(ctx: Ctx) -> None:
    assert len(await ctx.impl(JiraUserDirectory).assignable_users(project="PROJ")) == 2


@op("jira.filter.list")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JiraMetadataReader).list_filters()).items[0].name == "My open issues"


@op("jira.dashboard.list")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JiraMetadataReader).list_dashboards()).items[0].name == "Team"


# ------------------------------------------------------------------------------------------------------------------- agile
@op("jira.agile.board.list")
async def _(ctx: Ctx) -> None:
    board = (await ctx.impl(JiraAgileReader).list_boards()).items[0]
    assert (board.id, board.type) == (42, "scrum")


@op("jira.agile.board.get")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JiraAgileReader).get_board(42)).name == "PROJ board"
    with pytest.raises(NotFoundError):
        await ctx.impl(JiraAgileReader).get_board(999)


@op("jira.agile.board.config")
async def _(ctx: Ctx) -> None:
    assert [c["name"] for c in (await ctx.impl(JiraAgileReader).get_board_config(42)).columns] == ["To Do", "Done"]


@op("jira.agile.board.issues")
async def _(ctx: Ctx) -> None:
    assert [i.key for i in (await ctx.impl(JiraAgileReader).list_board_issues(42)).items] == ["PROJ-1", "PROJ-2"]


@op("jira.agile.sprint.list")
async def _(ctx: Ctx) -> None:
    sprint = (await ctx.impl(JiraAgileReader).list_sprints(42)).items[0]
    assert (sprint.id, sprint.state) == (7, "active")


@op("jira.agile.sprint.get")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JiraAgileReader).get_sprint(7)).name == "Sprint 7"


@op("jira.agile.sprint.issues")
async def _(ctx: Ctx) -> None:
    assert [i.key for i in (await ctx.impl(JiraAgileReader).list_sprint_issues(7)).items] == ["PROJ-1"]


@op("jira.agile.backlog.list")
async def _(ctx: Ctx) -> None:
    assert [i.key for i in (await ctx.impl(JiraAgileReader).list_backlog(42)).items] == ["PROJ-2"]


@op("jira.agile.epic.list")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JiraAgileReader).list_epics(42)).items[0].key == "PROJ-900"


@op("jira.agile.sprint.manage")
async def _(ctx: Ctx) -> None:
    sp = await ctx.impl(JiraAgileWriter).manage_sprint(SprintChange(action="create", board_id=42, name="Sprint 8"))
    assert sp.name == "Sprint 8" and sent(ctx, "POST", "/sprint")


@op("jira.agile.issue.move_to_sprint")
async def _(ctx: Ctx) -> None:
    await ctx.impl(JiraAgileWriter).move_issues_to_sprint(7, ["PROJ-2"])
    assert sent(ctx, "POST", "/sprint/7/issue")


@op("jira.agile.issue.rank")
async def _(ctx: Ctx) -> None:
    await ctx.impl(JiraAgileWriter).rank_issues(["PROJ-2"], before="PROJ-1")
    assert sent(ctx, "PUT", "/issue/rank")


@op("jira.agile.board.create")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JiraAgileWriter).create_board("Team board", filter_id=10)).name == "Team board"


# ------------------------------------------------------------------------------------------------------------------- JSM
@op("jsm.info")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JsmReader).info()).version == "1001.0.0"


@op("jsm.service_desk.list")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JsmReader).list_service_desks()).items[0].project_key == "OPS"


@op("jsm.request_type.list")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JsmReader).list_request_types("4")).items[0].name == "Get IT help"


@op("jsm.request.list")
async def _(ctx: Ctx) -> None:
    req = (await ctx.impl(JsmReader).list_requests(service_desk_id="4")).items[0]
    assert (req.issue_key, req.summary, req.status) == ("OPS-1", "VPN down", "Open")


@op("jsm.request.get")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JsmReader).get_request("OPS-1")).description == "Cannot connect"


@op("jsm.request.comments")
async def _(ctx: Ctx) -> None:
    r = ctx.impl(JsmReader)
    assert len((await r.list_request_comments("OPS-1")).items) == 2
    assert [c.id for c in (await r.list_request_comments("OPS-1", public_only=True)).items] == ["rc1"]


@op("jsm.request.sla")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JsmReader).get_request_sla("OPS-1"))[0].name == "Time to resolution"


@op("jsm.kb.search")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JsmReader).search_knowledge_base("vpn"))[0].title == "Fix VPN"


@op("jsm.organization.list")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JsmReader).list_organizations()).items[0].name == "Acme"


@op("jsm.customer.list")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JsmReader).list_customers("4")).items[0].display_name == "Bob Ray"


@op("jsm.request.create")
async def _(ctx: Ctx) -> None:
    req = await ctx.impl(JsmWriter).create_request("4", "r1", {"summary": "Printer broken", "description": "Jammed"})
    assert req.issue_key.startswith("OPS-")


@op("jsm.request.comment.add")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(JsmWriter).add_request_comment("OPS-1", "We are on it", public=True)).public is True


@op("jsm.request.transition")
async def _(ctx: Ctx) -> None:
    await ctx.impl(JsmWriter).transition_request("OPS-1", "711")
    assert sent(ctx, "POST", "/request/OPS-1/transition")


@op("jsm.request.approve")
async def _(ctx: Ctx) -> None:
    await ctx.impl(JsmWriter).answer_approval("OPS-1", "a1", approve=True)
    assert sent(ctx, "POST", "/request/OPS-1/approval/a1")


@op("jsm.customer.add")
async def _(ctx: Ctx) -> None:
    await ctx.impl(JsmWriter).add_customers("4", ["acc-new"])
    assert sent(ctx, "POST", "/servicedesk/4/customer")


# ------------------------------------------------------------------------------------------------------------------- Confluence
@op("confluence.content.get")
async def _(ctx: Ctx) -> None:
    page = await ctx.impl(ConfluenceContentReader).get_content("123")
    assert (page.id, page.title, page.version) == ("123", "Runbook", 3) and page.body_markdown == "# Runbook\n\nRestart **nginx**"
    with pytest.raises(NotFoundError):
        await ctx.impl(ConfluenceContentReader).get_content("999999")


@op("confluence.content.list")
async def _(ctx: Ctx) -> None:
    r = ctx.impl(ConfluenceContentReader)
    first = await r.list_content(limit=2)
    assert len(first.items) == 2 and first.next_cursor
    assert [p.title for p in (await r.list_content(title="Home")).items] == ["Home"]


@op("confluence.space.get")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(ConfluenceSpaceReader).get_space("ENG")).key == "ENG"


@op("confluence.space.list")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(ConfluenceSpaceReader).list_spaces()).items


@op("confluence.search.cql")
async def _(ctx: Ctx) -> None:
    page = await ctx.impl(ConfluenceSearcher).search_cql('type = page AND title ~ "Runbook"', limit=1)
    assert page.items and page.items[0].title == "Runbook"


@op("confluence.content.children")
async def _(ctx: Ctx) -> None:
    r = ctx.impl(ConfluenceContentReader)
    first = await r.list_children("123", limit=1)
    second = await r.list_children("123", limit=1, cursor=first.next_cursor)
    assert [c.id for c in first.items + second.items] == ["124", "125"]


@op("confluence.content.ancestors")
async def _(ctx: Ctx) -> None:
    assert [a.id for a in await ctx.impl(ConfluenceContentReader).list_ancestors("123")] == ["100"]


@op("confluence.content.descendants")
async def _(ctx: Ctx) -> None:
    assert {d.id for d in (await ctx.impl(ConfluenceContentReader).list_descendants("123", depth=2)).items} == {"124", "125"}


@op("confluence.content.versions")
async def _(ctx: Ctx) -> None:
    assert [v.number for v in (await ctx.impl(ConfluenceContentReader).list_versions("123")).items] == [3, 2]


@op("confluence.content.diff")
async def _(ctx: Ctx) -> None:
    diff = await ctx.impl(ConfluenceContentReader).diff_versions("123", 2, 3)
    assert "+Restart **nginx**" in diff.unified


@op("confluence.comment.list")
async def _(ctx: Ctx) -> None:
    assert {c.kind for c in (await ctx.impl(ConfluenceContentReader).list_comments("123")).items} == {"footer", "inline"}


@op("confluence.attachment.list")
async def _(ctx: Ctx) -> None:
    assert [a.title for a in (await ctx.impl(ConfluenceContentReader).list_attachments("123")).items] == ["diagram.png", "notes.txt"]


@op("confluence.attachment.download")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(ConfluenceContentReader).download_attachment("cat2")).data == b"notes"


@op("confluence.label.list")
async def _(ctx: Ctx) -> None:
    assert [lb.name for lb in await ctx.impl(ConfluenceContentReader).list_labels("123")] == ["runbook"]


@op("confluence.permission.get")
async def _(ctx: Ctx) -> None:
    assert {o["operation"] for o in (await ctx.impl(ConfluenceContentReader).get_permissions("123")).operations} == {"read", "update"}


@op("confluence.permission.restrictions.get")
async def _(ctx: Ctx) -> None:
    res = await ctx.impl(ConfluenceContentReader).get_restrictions("123")
    assert res.restricted and res.read_groups == ["eng"]


@op("confluence.space.permissions.list")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(ConfluenceSpaceReader).list_space_permissions("ENG")).items is not None


@op("confluence.task.list")
async def _(ctx: Ctx) -> None:
    assert {t.id for t in (await ctx.impl(ConfluenceContentReader).list_tasks(content_id="123")).items} == {"t1", "t2"}


@op("confluence.template.list")
async def _(ctx: Ctx) -> None:
    assert await ctx.impl(ConfluenceTemplateReader).list_templates()


@op("confluence.template.get")
async def _(ctx: Ctx) -> None:
    t = ctx.impl(ConfluenceTemplateReader)
    assert (await t.get_template((await t.list_templates())[0].id)).id


@op("confluence.content.macros.resolve")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(ConfluenceContentReader).resolve_macros("123")).id == "123"


@op("confluence.content.create")
async def _(ctx: Ctx) -> None:
    made = await ctx.impl(ConfluenceContentWriter).create_content(ContentDraft(title="New page", space="ENG", body="# Hi", parent_id="100"))
    assert made.title == "New page"


@op("confluence.content.update")
async def _(ctx: Ctx) -> None:
    upd = await ctx.impl(ConfluenceContentWriter).update_content("123", ContentUpdate(title="Renamed", version=3))
    assert upd.title == "Renamed" and upd.version == 4


@op("confluence.content.move")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(ConfluenceContentWriter).move_content("125", target_parent_id="100")).parent_id == "100"


@op("confluence.content.copy")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(ConfluenceContentWriter).copy_content("123", destination_parent_id="100", title="Copy")).title == "Copy"


@op("confluence.content.archive")
async def _(ctx: Ctx) -> None:
    await ctx.impl(ConfluenceContentWriter).archive_content(["124"])
    assert (await ctx.impl(ConfluenceContentReader).get_content("124")).status == "archived"


@op("confluence.content.delete")
async def _(ctx: Ctx) -> None:
    await ctx.impl(ConfluenceContentDeleter).delete_content("125")
    with pytest.raises(NotFoundError):
        await ctx.impl(ConfluenceContentReader).get_content("125")


# ------------------------------------------------------------------------------------------------------------------- graph
@op("graph.context")
async def _(ctx: Ctx) -> None:
    out = await ctx.impl(TeamworkGraphReader).get_context("ari:cloud:jira::issue/10001")
    assert out.root.endswith("10001") and out.related[0].title == "Login fails"


@op("graph.object.get")
async def _(ctx: Ctx) -> None:
    assert [o.ari for o in await ctx.impl(TeamworkGraphReader).get_objects(["ari:cloud:jira::issue/10001"])] == ["ari:cloud:jira::issue/10001"]


@op("graph.search.semantic")
async def _(ctx: Ctx) -> None:
    assert (await ctx.impl(SemanticSearcher).semantic_search("runbook"))[0].title == "Runbook"


# ------------------------------------------------------------------------------------------------------------------- exports
@op("export.jira.csv")
async def _(ctx: Ctx) -> None:
    dest = Path(mkdtemp()) / "issues.csv"
    await ctx.impl(JiraExporter).export_issues_csv("project = PROJ", dest=dest)
    lines = dest.read_text().splitlines()
    assert lines[0].startswith("key,summary") and any(line.startswith("PROJ-1,Login fails") for line in lines)


@op("export.confluence.page")
async def _(ctx: Ctx) -> None:
    dest = Path(mkdtemp()) / "page.html"
    await ctx.impl(ConfluenceExporter).export_content("123", format="html", dest=dest)
    text = dest.read_text()
    assert "<h1>Runbook</h1>" in text and "nginx" in text


# ------------------------------------------------------------------------------------------------------------------- GraphQL executor
@op("graph.graphql.execute")
async def _(ctx: Ctx) -> None:
    ex = ctx.impl(GraphQLExecutor)
    assert (await ex.execute("{ __typename }"))["data"] == {"__typename": "Query"}
    with pytest.raises(PolicyDeniedError) as exc:
        await ex.execute("mutation M { jira { createIssue } }")
    assert "READ" in str(exc.value)


# ------------------------------------------------------------------------------------------------------------------- admin webhooks
@op("webhook.jira.admin.list")
async def _(ctx: Ctx) -> None:
    from atlian_assiant.protocols import AdminWebhookManager

    ctx.server.admin_webhooks.append({"name": "hook", "url": "https://hooks.example.com/jira", "events": ["jira:issue_created"], "enabled": True,
                                      "self": "https://example.atlassian.net/rest/webhooks/1.0/webhook/7"})
    hooks = await ctx.impl(AdminWebhookManager).list_admin_webhooks()
    assert [(h.id, h.url, h.events) for h in hooks] == [("7", "https://hooks.example.com/jira", ["jira:issue_created"])]


@op("export.confluence.space")
async def _(ctx: Ctx) -> None:
    dest = Path(mkdtemp()) / "space.zip"
    await ctx.impl(ConfluenceExporter).export_space("ENG", format="html", dest=dest)
    assert dest.exists() and b"ENG" in dest.read_bytes()


# ------------------------------------------------------------------------------------------------------------------- analytics (Data Lake)
@op("analytics.share.list")
async def _(ctx: Ctx) -> None:
    shares = await ctx.impl(AnalyticsReader).list_shares()
    assert "atlassian" in shares or "share1" in shares


@op("analytics.table.list")
async def _(ctx: Ctx) -> None:
    tables = await ctx.impl(AnalyticsReader).list_tables()
    assert any(t.endswith("jira_issue") for t in tables)


@op("analytics.table.load")
async def _(ctx: Ctx) -> None:
    r = ctx.impl(AnalyticsReader)
    rows = await r.load_table("jira_issue")
    assert rows and ({x.get("key") or x.get("issue_key") for x in rows} >= {"PROJ-1"})
    assert len(await r.load_table("jira_issue", limit=1)) == 1


@op("analytics.table.query")
async def _(ctx: Ctx) -> None:
    rows = await ctx.impl(AnalyticsReader).query("SELECT status, COUNT(*) AS n FROM jira_issue GROUP BY status ORDER BY status", tables=["jira_issue"])
    assert rows and isinstance(rows, list)
