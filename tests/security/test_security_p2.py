"""Executable evidence for SEC-06 (destructive actions) from design section 17."""
import pytest

from atlian_assiant import Access
from atlian_assiant import operations as ops
from atlian_assiant.core.errors import ConfirmationDeclinedError, PolicyDeniedError
from atlian_assiant.policy import AccessPolicy, DenyAll, PolicyGuard
from atlian_assiant.testing.mcp_fake import ALL_TOOLS
from tests.agent.conftest import build
from tests.contract.mcp_helpers import make_mcp

pytestmark = pytest.mark.anyio

DESTRUCTIVE = [ops.get(o) for o in ("jira.issue.delete", "jira.issue.comment.delete", "jira.issue.attachment.delete", "confluence.content.delete",
                                    "confluence.comment.delete", "confluence.attachment.delete", "jira.project.create", "jira.project.update",
                                    "jira.admin.screen.update")]


async def test_sec_06_delete_and_manage_are_denied_by_default_and_by_read_write_policies():
    for pol in (AccessPolicy(), AccessPolicy.read_write()):
        for op in DESTRUCTIVE:
            with pytest.raises(PolicyDeniedError):
                await PolicyGuard(pol).authorize(op, ("X-1",), {})


async def test_sec_06_destructive_actions_are_confirmed_even_if_the_policy_asks_for_no_confirmation():
    pol = AccessPolicy(allowed=frozenset(Access), confirm=frozenset())              # a permissive policy that "forgot" confirmation
    for op in DESTRUCTIVE:
        with pytest.raises(ConfirmationDeclinedError):
            await PolicyGuard(pol, None).authorize(op, ("X-1",), {})                 # no handler: refused, never silently executed
        with pytest.raises(ConfirmationDeclinedError):
            await PolicyGuard(pol, DenyAll()).authorize(op, ("X-1",), {})
    plain_write = ops.JIRA_ISSUE_CREATE                                              # ordinary writes may be unconfirmed when the policy says so
    assert (await PolicyGuard(pol, None).authorize(plain_write, ("PROJ",), {})).plan is None


async def test_sec_06_a_destructive_dry_run_warns_and_changes_nothing():
    c = build(AccessPolicy(allowed=frozenset({Access.READ, Access.DELETE})))
    plan = await c.jira.issues.delete("PROJ-3", dry_run=True)
    assert "destructive" in plan.warnings[0] and plan.reversible is False
    assert (await c.jira.issues.get("PROJ-3")).key == "PROJ-3"


async def test_sec_06_mcp_delete_and_manage_groups_disappear_when_the_admin_has_not_enabled_them():
    delete_tools = {"deleteJiraIssue", "deleteJiraComment", "deleteJiraIssueAttachment"}
    manage_tools = {"createJiraProject", "updateJiraProject", "getJiraWorkflows", "getJiraWorkflowEditorLink", "getJiraScreen", "updateJiraScreen"}
    mcp, _ = make_mcp(enabled_tools=ALL_TOOLS - delete_tools - manage_tools)
    live = await mcp.connect()
    for op in ("jira.issue.delete", "jira.issue.comment.delete", "jira.issue.attachment.delete", "jira.project.create", "jira.project.update",
               "jira.admin.screen.update"):
        assert op not in live, op
    assert "jira.issue.create" in live and "jira.issue.get" in live


async def test_sec_06_tools_for_destructive_actions_are_hidden_without_delete_access():
    names = {t["name"] for t in build(AccessPolicy.read_write()).tools.export("anthropic")}
    assert "jira_delete_issue" not in names and "confluence_delete_page" not in names and "jira_create_issue" in names
    names = {t["name"] for t in build(AccessPolicy(allowed=frozenset({Access.READ, Access.DELETE}))).tools.export("anthropic")}
    assert {"jira_delete_issue", "confluence_delete_page"} <= names
