import pytest

from atlassian_client import operations as ops
from atlassian_client.capabilities import BackendKind as B
from atlassian_client.core.errors import (AtlassianError, BackendUnavailableError, DeploymentMismatchError, NoBackendAvailableError,
                                          NotFoundError, UnsupportedOperationError)
from atlassian_client.core.pagination import Cursor
from atlassian_client.protocols import JiraIssueReader, JiraIssueSearcher, implementation_of
from atlassian_client.routing import PRESET_ORDERS, CircuitBreaker, Preset, Router, SelectionPolicy
from atlassian_client.testing import FakeBackend

pytestmark = [pytest.mark.layer_09]

GET = ops.JIRA_ISSUE_GET
SEARCH = ops.JIRA_ISSUE_SEARCH


class Flaky(FakeBackend):
    """A backend that fails in a configurable way before touching anything."""

    def __init__(self, kind, exc):
        super().__init__(kind)
        self.exc, self.hits = exc, 0
        orig = self.jira.get_issue

        async def get_issue(key, **kw):
            self.hits += 1
            if self.exc:
                raise self.exc
            return await orig(key, **kw)
        self.jira.get_issue = get_issue


def router(backends, order=(B.MCP, B.REST), **kw):
    return Router({b.kind: b for b in backends}, SelectionPolicy({"*": order}), **kw)


def test_selection_policy_exact_then_longest_prefix_then_default():
    p = SelectionPolicy({"*": (B.REST,), "jira.*": (B.MCP,), "jira.issue.*": (B.ACLI,), "jira.issue.get": (B.TWG,)})
    assert p.order_for(GET) == (B.TWG,) and p.order_for(SEARCH) == (B.ACLI,)
    assert p.order_for(ops.get("jira.project.get")) == (B.MCP,) and p.order_for(ops.get("jsm.info")) == (B.REST,)
    with pytest.raises(ValueError):
        SelectionPolicy({"jira.*": (B.REST,)})


def test_every_preset_defines_a_default_order():
    assert set(PRESET_ORDERS) == set(Preset) and all("*" in v for v in PRESET_ORDERS.values())
    assert SelectionPolicy.from_preset(Preset.AGENT).order_for(GET)[0] is B.MCP
    assert SelectionPolicy.from_preset(Preset.SERVICE).order_for(GET)[0] is B.REST
    assert SelectionPolicy.from_preset(Preset.AGENT).order_for(ops.get("jira.jql.parse")) == (B.REST,)
    assert SelectionPolicy.from_preset(Preset.SERVICE, {"jira.issue.get": (B.MCP,)}).order_for(GET) == (B.MCP,)


@pytest.mark.anyio
async def test_falls_back_when_unavailable_or_unsupported_and_stamps_source():
    mcp, rest = Flaky(B.MCP, BackendUnavailableError("no creds")), Flaky(B.REST, None)
    r = router([mcp, rest])
    issue = await r.call(GET, JiraIssueReader, "get_issue", "PROJ-1")
    assert issue.key == "PROJ-1" and issue.source.backend is B.REST and (mcp.hits, rest.hits) == (1, 1)
    mcp2 = Flaky(B.MCP, UnsupportedOperationError("no such tool"))
    assert (await router([mcp2, Flaky(B.REST, None)]).call(GET, JiraIssueReader, "get_issue", "PROJ-1")).source.backend is B.REST


@pytest.mark.anyio
async def test_domain_errors_are_returned_not_retried_elsewhere():
    mcp, rest = Flaky(B.MCP, NotFoundError("gone")), Flaky(B.REST, None)
    with pytest.raises(NotFoundError):
        await router([mcp, rest]).call(GET, JiraIssueReader, "get_issue", "PROJ-1")
    assert rest.hits == 0


@pytest.mark.anyio
async def test_other_errors_propagate_without_fallback():
    mcp, rest = Flaky(B.MCP, AtlassianError("server exploded after the request was sent")), Flaky(B.REST, None)
    with pytest.raises(AtlassianError):
        await router([mcp, rest]).call(GET, JiraIssueReader, "get_issue", "PROJ-1")
    assert rest.hits == 0


@pytest.mark.anyio
async def test_all_failing_raises_aggregate_with_attempts_and_hint():
    r = router([Flaky(B.MCP, BackendUnavailableError("a")), Flaky(B.REST, UnsupportedOperationError("b"))])
    with pytest.raises(NoBackendAvailableError) as exc:
        await r.call(GET, JiraIssueReader, "get_issue", "PROJ-1")
    assert [a[0] for a in exc.value.details["attempts"]] == ["mcp", "rest"] and "Tried" in exc.value.hint


@pytest.mark.anyio
async def test_nothing_configured_hint_suggests_describe():
    with pytest.raises(NoBackendAvailableError) as exc:
        await router([]).call(GET, JiraIssueReader, "get_issue", "PROJ-1")
    assert "describe" in exc.value.hint


@pytest.mark.anyio
async def test_forced_backend_disables_fallback_and_view_works():
    mcp, rest = Flaky(B.MCP, BackendUnavailableError("down")), Flaky(B.REST, None)
    r = router([mcp, rest])
    with pytest.raises(NoBackendAvailableError):
        await r.call(GET, JiraIssueReader, "get_issue", "PROJ-1", backend=B.MCP)
    assert rest.hits == 0
    assert (await r.forced("rest").call(GET, JiraIssueReader, "get_issue", "PROJ-1")).source.backend is B.REST


@pytest.mark.anyio
async def test_cursor_pins_the_backend_that_produced_it():
    a, b = FakeBackend(B.MCP), FakeBackend(B.REST)
    r = router([a, b])
    page = await b.jira.search_issues("x", limit=1)
    assert Cursor.decode(page.next_cursor).backend is B.REST
    out = await r.call(SEARCH, JiraIssueSearcher, "search_issues", "x", limit=1, cursor=page.next_cursor)
    assert out.source.backend is B.REST                      # not MCP, even though MCP is first in the order


@pytest.mark.anyio
async def test_circuit_breaker_skips_a_dead_backend_then_probes_again():
    now = {"t": 0.0}
    br = CircuitBreaker(threshold=2, cooldown=10, clock=lambda: now["t"])
    mcp, rest = Flaky(B.MCP, BackendUnavailableError("down")), Flaky(B.REST, None)
    r = router([mcp, rest], breaker=br)
    for _ in range(3):
        await r.call(GET, JiraIssueReader, "get_issue", "PROJ-1")
    assert mcp.hits == 2                                         # breaker opened after 2 failures
    now["t"] = 11
    await r.call(GET, JiraIssueReader, "get_issue", "PROJ-1")
    assert mcp.hits == 3                                         # half-open probe
    br.record_success(B.MCP)
    assert not br.is_open(B.MCP)


@pytest.mark.anyio
async def test_cloud_only_operation_on_data_center_is_a_deployment_mismatch():
    r = Router({B.REST: FakeBackend()}, SelectionPolicy({"*": (B.REST,)}), deployment_cloud=False)
    with pytest.raises(DeploymentMismatchError):
        await r.call(ops.GRAPH_CONTEXT, JiraIssueReader, "x")


@pytest.mark.anyio
async def test_unsupported_operation_is_not_routed_and_guard_runs_first():
    seen = []

    class Guard:
        async def authorize(self, op, args, kwargs):
            seen.append(op.id)
            raise AtlassianError("denied")

    only_search = FakeBackend(B.REST, operations=frozenset({"jira.issue.search"}))
    r = Router({B.REST: only_search}, SelectionPolicy({"*": (B.REST,)}), guard=Guard())
    with pytest.raises(AtlassianError, match="denied"):
        await r.call(GET, JiraIssueReader, "get_issue", "PROJ-1")
    assert seen == ["jira.issue.get"]
    assert Router({B.REST: only_search}, SelectionPolicy({"*": (B.REST,)})).candidates(GET) == []


def test_implementation_of_prefers_composition_hook_then_structural():
    fb = FakeBackend()
    assert implementation_of(fb, JiraIssueReader) is fb.jira

    class Plain:
        async def get_issue(self, key, **kw): ...
        async def list_comments(self, *a, **k): ...
        async def list_changelog(self, *a, **k): ...
        async def list_worklogs(self, *a, **k): ...
        async def list_transitions(self, *a, **k): ...
        async def list_attachments(self, *a, **k): ...
        async def download_attachment(self, *a, **k): ...
        async def list_links(self, *a, **k): ...
        async def list_remote_links(self, *a, **k): ...
        async def list_watchers(self, *a, **k): ...
        async def get_property(self, *a, **k): ...

    p = Plain()
    assert implementation_of(p, JiraIssueReader) is p and implementation_of(object(), JiraIssueReader) is None
