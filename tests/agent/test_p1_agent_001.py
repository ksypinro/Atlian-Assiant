import json

import pytest

from atlian_assiant import Access
from atlian_assiant import operations as ops
from atlian_assiant.agent.tools import (
    CURATED,
    ToolResult,
    ToolSpec,
    agent_tool,
    build_input_model,
    param_docs,
    render_result,
)
from atlian_assiant.models.common import Page
from atlian_assiant.models.jira import Issue
from atlian_assiant.policy import AccessPolicy

from .conftest import build

pytestmark = pytest.mark.anyio


def test_curated_catalog_has_valid_unique_names_and_resolvable_paths(client):
    names = [s.name for s in CURATED]
    assert len(names) == len(set(names)) == 58
    import re
    assert all(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", n) for n in names)
    for spec in CURATED:
        assert client.tools._fn(spec) is not None


def test_exporters_produce_the_four_formats_consistently(client):
    a, o, m, j = (client.tools.export(f) for f in ("anthropic", "openai", "mcp", "json_schema"))
    assert len(a) == len(o) == len(m) == len(j) == 39
    first = a[2]
    assert set(first) == {"name", "description", "input_schema"}
    assert o[2]["type"] == "function" and o[2]["function"]["parameters"] == first["input_schema"] and o[2]["function"]["name"] == first["name"]
    assert m[2]["inputSchema"] == first["input_schema"] and j[2]["schema"] == first["input_schema"]
    json.dumps(a)                                          # fully JSON-serializable


def test_credit_consuming_tools_are_hidden_unless_allowed():
    visible = {t["name"] for t in build(AccessPolicy()).tools.export("anthropic")}
    assert "atlassian_semantic_search" not in visible and "atlassian_graph_context" not in visible and "jira_get_issue" in visible
    assert {"atlassian_semantic_search", "atlassian_graph_context"} <= {t["name"] for t in build().tools.export("anthropic")}
    assert len(build(AccessPolicy()).tools.list(include_hidden=True)) == 58
    assert len(build(AccessPolicy()).tools.export("anthropic")) == 37


def test_list_can_filter_by_access_class(client):
    searches = {s.name for s in client.tools.list(access=[Access.SEARCH])}
    assert {"jira_search_issues", "confluence_search_cql", "jsm_search_kb", "atlassian_semantic_search"} <= searches
    assert "jira_get_issue" not in searches


async def test_hidden_tool_cannot_be_invoked_even_if_named():
    r = await build(AccessPolicy()).tools.ainvoke("atlassian_semantic_search", {"query": "x"})
    assert not r.ok and r.error["type"] == "PolicyDeniedError"


async def test_errors_become_results_with_hints_and_never_raise(client):
    r = await client.tools.ainvoke("jira_get_issue", {"key": "PROJ-404"})
    assert not r.ok and r.error["type"] == "NotFoundError" and "permission" in r.error["hint"].lower() and "Hint:" in r.text
    r = await client.tools.ainvoke("nope_tool", {})
    assert not r.ok and r.error["type"] == "UnknownTool"
    r = await client.tools.ainvoke("jira_get_issue", {"key": 5, "bogus": 1})
    assert not r.ok and r.error["type"] == "InvalidArguments"


async def test_pagination_cursor_round_trips_through_tools(client):
    first = await client.tools.ainvoke("jira_search_issues", {"jql": "project = PROJ", "limit": 2})
    assert first.next_cursor and f"next_cursor={first.next_cursor}" in first.text
    second = await client.tools.ainvoke("jira_search_issues", {"jql": "project = PROJ", "limit": 2, "cursor": first.next_cursor})
    assert second.ok and second.next_cursor is None and "end of results" in second.text
    assert first.backend == "mcp"                                           # agent preset routes to MCP first


async def test_output_is_truncated_with_an_explicit_marker(client):
    r = await client.tools.ainvoke("confluence_get_page", {"content_id": "123"}, max_chars=40)
    assert r.ok and r.truncated and "…[truncated" in r.text


async def test_data_is_json_serializable_and_excludes_raw_payloads(client):
    r = await client.tools.ainvoke("jira_get_issue", {"key": "PROJ-1"})
    blob = json.dumps(r.data)
    assert '"key": "PROJ-1"' in blob and '"raw"' not in blob


def test_render_result_variants():
    assert render_result(None) == "OK (no content)."
    assert render_result(Page[int](items=[], next_cursor=None)).startswith("(no results)")
    assert render_result([Issue(id="1", key="P-1")]).startswith("**P-1**")
    assert render_result({"a": 1}).strip().startswith("{") and render_result("plain") == "plain"


def test_agent_tool_decorator_attaches_metadata():
    @agent_tool(ops.JIRA_ISSUE_GET, when_to_use="x", not_for="y", examples=["e"])
    async def f(self, key: str) -> None: ...

    spec = f.__agent_tool__
    assert isinstance(spec, ToolSpec) and spec.name == "jira_issue_get" and spec.not_for == "y" and spec.operation is ops.JIRA_ISSUE_GET


def test_param_docs_and_input_model(client):
    fn = client.jira.issues.search
    docs = param_docs(fn)
    assert "JQL query" in docs["jql"] and "next_cursor" in docs["cursor"]
    model = build_input_model(ToolSpec("t", None, "x", hidden_params=frozenset({"backend", "reconcile_issue_ids"})), fn)
    assert set(model.model_fields) == {"jql", "fields", "limit", "cursor"} and model.model_fields["jql"].is_required()


def test_sync_client_exposes_blocking_invoke():
    from atlian_assiant import AtlassianClient

    atl = AtlassianClient(build())
    try:
        r = atl.tools.invoke("jira_get_issue", {"key": "PROJ-1"})
        assert isinstance(r, ToolResult) and r.ok
        assert len(atl.tools.export("anthropic")) == 39
    finally:
        atl.close()
