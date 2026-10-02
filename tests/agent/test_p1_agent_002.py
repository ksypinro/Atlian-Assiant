import pytest

from atlassian_client import Access, AtlassianConfig, BackendKind, ClientFactory, operations as ops
from atlassian_client.core.errors import ConfigurationError, ConfirmationDeclinedError, PolicyDeniedError
from atlassian_client.policy import (MCP_SCOPES, AccessPolicy, AutoApprove, CallbackConfirmation, DenyAll, PlannedAction, PolicyGuard,
                                     scope_cql, scope_jql, scopes_for)
from atlassian_client.testing import FakeBackend

from .conftest import build

pytestmark = pytest.mark.anyio


def guard(policy=None, confirm=None, **kw):
    return PolicyGuard(policy or AccessPolicy(), confirm, **kw)


async def test_default_policy_allows_read_and_search_only():
    g = guard()
    for op in (ops.JIRA_ISSUE_GET, ops.JIRA_ISSUE_SEARCH):
        assert (await g.authorize(op, ("X-1",), {})).plan is None
    for op in (ops.JIRA_ISSUE_CREATE, ops.JIRA_ISSUE_DELETE, ops.JIRA_PROJECT_CREATE, ops.ANALYTICS_LOAD_TABLE, ops.ORG_EVENTS_STREAM):
        with pytest.raises(PolicyDeniedError) as exc:
            await g.authorize(op, (), {})
        assert op.access.name in exc.value.hint


async def test_credit_consuming_operations_need_an_explicit_opt_in():
    with pytest.raises(PolicyDeniedError) as exc:
        await guard().authorize(ops.GRAPH_SEMANTIC_SEARCH, ("q",), {})
    assert "allow_credit_consuming" in exc.value.hint
    await guard(AccessPolicy(allow_credit_consuming=True)).authorize(ops.GRAPH_SEMANTIC_SEARCH, ("q",), {})


async def test_cloud_id_must_be_allowed():
    g = guard(AccessPolicy(allowed_cloud_ids=frozenset({"mine"})), cloud_id="theirs")
    with pytest.raises(PolicyDeniedError):
        await g.authorize(ops.JIRA_ISSUE_GET, ("X-1",), {})
    await guard(AccessPolicy(allowed_cloud_ids=frozenset({"mine"})), cloud_id="mine").authorize(ops.JIRA_ISSUE_GET, ("X-1",), {})


def test_jql_and_cql_scoping_preserves_order_by_and_empty_queries():
    assert scope_jql("status = Open", frozenset({"B", "A"})) == "(status = Open) AND project in (A, B)"
    assert scope_jql("status = Open order by updated DESC", frozenset({"A"})) == "(status = Open) AND project in (A) order by updated DESC"
    assert scope_jql("", frozenset({"A"})) == "project in (A)" and scope_jql("ORDER BY key", frozenset({"A"})) == "project in (A) ORDER BY key"
    assert scope_cql('text ~ "x"', frozenset({"ENG"})) == '(text ~ "x") AND space in ("ENG")'


async def test_project_scope_rewrites_search_and_blocks_other_projects():
    g = guard(AccessPolicy(allowed_projects=frozenset({"PROJ"})))
    d = await g.authorize(ops.JIRA_ISSUE_SEARCH, ("assignee = me",), {})
    assert d.args == ("(assignee = me) AND project in (PROJ)",)
    d = await g.authorize(ops.get("jira.agile.board.issues"), (42,), {})
    assert d.kwargs["jql"] == "project in (PROJ)"
    await g.authorize(ops.JIRA_ISSUE_GET, ("PROJ-1",), {})
    with pytest.raises(PolicyDeniedError):
        await g.authorize(ops.JIRA_ISSUE_GET, ("SECRET-1",), {})
    with pytest.raises(PolicyDeniedError) as exc:
        await g.authorize(ops.JIRA_ISSUE_GET, ("10001",), {})
    assert "keys" in exc.value.hint
    with pytest.raises(PolicyDeniedError) as exc:
        await g.authorize(ops.get("jira.issue.attachment.download"), ("att1",), {})
    assert "jira_get_issue" in exc.value.hint
    await g.authorize(ops.get("jira.project.get"), ("PROJ",), {})
    with pytest.raises(PolicyDeniedError):
        await g.authorize(ops.get("jira.project.get"), ("OPS",), {})


async def test_space_scope_rewrites_cql_and_blocks_by_id_reads():
    g = guard(AccessPolicy(allowed_spaces=frozenset({"ENG"})))
    d = await g.authorize(ops.CONF_SEARCH_CQL, ("text ~ x",), {})
    assert d.args == ('(text ~ x) AND space in ("ENG")',)
    with pytest.raises(PolicyDeniedError) as exc:
        await g.authorize(ops.CONF_CONTENT_GET, ("123",), {})
    assert "confluence_search_cql" in exc.value.hint
    d = await g.authorize(ops.get("confluence.space.list"), (), {"keys": ["ENG", "OPS"]})
    assert d.kwargs["keys"] == ["ENG"]
    assert (await g.authorize(ops.get("confluence.space.list"), (), {})).kwargs["keys"] == ["ENG"]


async def test_limits_and_attachment_sizes_are_clamped():
    g = guard(AccessPolicy(max_results_per_call=10, max_attachment_bytes=100))
    assert (await g.authorize(ops.JIRA_ISSUE_SEARCH, ("x",), {"limit": 500})).kwargs["limit"] == 10
    assert (await g.authorize(ops.JIRA_ISSUE_SEARCH, ("x",), {"limit": 5})).kwargs["limit"] == 5
    dl = ops.get("jira.issue.attachment.download")
    assert (await g.authorize(dl, ("a",), {})).kwargs["max_bytes"] == 100
    assert (await g.authorize(dl, ("a",), {"max_bytes": 1000})).kwargs["max_bytes"] == 100
    assert (await g.authorize(dl, ("a",), {"max_bytes": 7})).kwargs["max_bytes"] == 7


async def test_writes_dry_run_returns_the_plan_and_removes_the_flag():
    g = guard(AccessPolicy.read_write(), DenyAll())
    d = await g.authorize(ops.JIRA_ISSUE_TRANSITION, ("PROJ-1", "Done"), {"dry_run": True})
    assert isinstance(d.plan, PlannedAction) and d.plan.target == "PROJ-1" and "PROJ-1" in d.plan.summary
    assert d.plan.arguments["args"] == ["PROJ-1", "Done"] and not d.plan.reversible                      # non-idempotent
    d = await g.authorize(ops.get("jira.issue.edit"), ("PROJ-1",), {"dry_run": True, "backend": "rest"})
    assert d.plan.reversible and "dry_run" not in d.plan.arguments


async def test_writes_need_confirmation_and_declines_are_final():
    pol = AccessPolicy.read_write()
    seen = []

    async def yes(plan):
        seen.append(plan.summary)
        return True

    d = await guard(pol, CallbackConfirmation(yes)).authorize(ops.JIRA_ISSUE_CREATE, ("PROJ",), {})
    assert d.plan is None and len(seen) == 1
    with pytest.raises(ConfirmationDeclinedError) as exc:
        await guard(pol, DenyAll()).authorize(ops.JIRA_ISSUE_CREATE, ("PROJ",), {})
    assert "Do not retry" in exc.value.hint
    with pytest.raises(ConfirmationDeclinedError) as exc:
        await guard(pol, None).authorize(ops.JIRA_ISSUE_CREATE, ("PROJ",), {})
    assert "dry_run=True" in exc.value.hint
    assert (await CallbackConfirmation(lambda p: True).confirm(None)) is True                 # sync callbacks work too


async def test_delete_plans_carry_a_destructive_warning_and_read_ops_ignore_dry_run():
    pol = AccessPolicy(allowed=frozenset({Access.READ, Access.DELETE}))
    d = await guard(pol, DenyAll()).authorize(ops.JIRA_ISSUE_DELETE, ("PROJ-1",), {"dry_run": True})
    assert "destructive" in d.plan.warnings[0]
    d = await guard().authorize(ops.JIRA_ISSUE_GET, ("PROJ-1",), {"dry_run": True})
    assert d.plan is None and d.kwargs["dry_run"] is False


async def test_confirmation_not_required_when_the_policy_does_not_ask_for_it():
    pol = AccessPolicy(allowed=frozenset({Access.READ, Access.WRITE}), confirm=frozenset())
    assert (await guard(pol, None).authorize(ops.JIRA_ISSUE_CREATE, ("PROJ",), {})).plan is None


async def test_auto_approve_is_refused_for_the_agent_preset():
    with pytest.raises(ConfigurationError) as exc:
        ClientFactory().build_async(AtlassianConfig(site="x"), backends=[], preset="agent", confirm=AutoApprove())
    assert "AutoApprove" in str(exc.value)
    ClientFactory().build_async(AtlassianConfig(site="x"), backends=[], preset="service", confirm=AutoApprove())


async def test_guard_runs_inside_the_router_so_clients_cannot_bypass_it():
    c = build(AccessPolicy(allowed_projects=frozenset({"PROJ"})))
    with pytest.raises(PolicyDeniedError):
        await c.jira.issues.get("SECRET-1")
    assert (await c.jira.issues.get("PROJ-1")).key == "PROJ-1"
    sec = await c.tools.ainvoke("jira_get_issue", {"key": "SECRET-1"})
    assert not sec.ok and sec.error["type"] == "PolicyDeniedError"
    limited = build(AccessPolicy(max_results_per_call=1))
    assert len((await limited.jira.issues.search("x", limit=50)).items) == 1


async def test_with_policy_returns_a_view_under_a_new_policy():
    c = build()
    narrow = c.with_policy(AccessPolicy(allowed_projects=frozenset({"OPS"})))
    with pytest.raises(PolicyDeniedError):
        await narrow.jira.issues.get("PROJ-1")
    assert (await c.jira.issues.get("PROJ-1")).key == "PROJ-1"          # original untouched
    assert narrow.policy.allowed_projects == frozenset({"OPS"})


def test_scopes_follow_the_policy_least_privilege():
    ro = scopes_for(AccessPolicy())
    assert ro == sorted(["read:jira:agent-interface", "search:jira:agent-interface", "read:confluence:agent-interface",
                         "search:confluence:agent-interface", "read:all:twg"])
    assert not any(s.startswith(("write:", "delete:", "manage:")) for s in ro) and "search:rovo:agent-interface" not in ro
    rw = scopes_for(AccessPolicy.read_write(), include_rovo=True)
    assert "write:jira:agent-interface" in rw and "write:all:twg" in rw and "search:rovo:agent-interface" in rw
    assert "delete:jira:agent-interface" not in rw and set(MCP_SCOPES) == {"jira", "confluence", "graph"}
