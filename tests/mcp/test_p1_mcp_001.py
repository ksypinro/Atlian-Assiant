"""MCP session lifecycle, live tool list, tool map and result mapping (layer 4)."""
import httpx
import pytest

from atlassian_client import AtlassianConfig, BackendKind, ClientFactory, operations as ops
from atlassian_client.backends.mcp import OP_TOOLS, McpBackend, McpToolResult, SdkMcpTransport
from atlassian_client.core.errors import (ConfigurationError, NoBackendAvailableError, NotFoundError, PermissionDeniedError,
                                          ServerError, UnsupportedOperationError)
from atlassian_client.protocols import JiraIssueReader
from atlassian_client.testing import FakeBackend
from atlassian_client.testing.mcp_fake import ALL_TOOLS, FakeMcpTransport
from tests.contract.mcp_helpers import CFG, make_mcp

pytestmark = [pytest.mark.layer_04, pytest.mark.anyio]


def test_tool_map_covers_the_documented_read_search_tools_and_only_catalogued_operations():
    assert set(OP_TOOLS) <= set(ops.CATALOG), sorted(set(OP_TOOLS) - set(ops.CATALOG))
    for tool in ("getJiraIssue", "searchJiraIssuesUsingJql", "getConfluenceContent", "searchConfluence", "getTeamworkGraphContext",
                 "listJiraBoards", "diffConfluenceContentVersions", "atlassianUserInfo", "getAccessibleAtlassianResources", "search"):
        assert tool in OP_TOOLS.values(), tool
    assert all(ops.CATALOG[o].access.value in ("read", "search") for o in OP_TOOLS)


async def test_cloud_id_is_resolved_once_from_accessible_resources_and_injected_into_every_call():
    mcp, t = make_mcp()
    await mcp.jira.get_issue("PROJ-1")
    await mcp.jira.get_issue("PROJ-2")
    assert [c[0] for c in t.calls].count("getAccessibleAtlassianResources") == 1
    assert all(c[1].get("cloudId") == "cloud-1" for c in t.calls if c[0] != "getAccessibleAtlassianResources")


async def test_configured_cloud_id_skips_discovery_and_unknown_site_is_a_clear_error():
    t = FakeMcpTransport()
    mcp = McpBackend(t, AtlassianConfig(site="example.atlassian.net", cloud_id="cloud-1", email="a@b.c", api_token="tok-123456"))
    await mcp.jira.get_issue("PROJ-1")
    assert "getAccessibleAtlassianResources" not in [c[0] for c in t.calls]
    bad = McpBackend(FakeMcpTransport(), AtlassianConfig(site="other-tenant.atlassian.net", email="a@b.c", api_token="tok-123456"))
    t2 = bad.transport
    t2.handlers["getAccessibleAtlassianResources"] = lambda x: [{"id": "a", "url": "https://one"}, {"id": "b", "url": "https://two"}]
    with pytest.raises(ConfigurationError) as exc:
        await bad.jira.get_issue("PROJ-1")
    assert "ATLASSIAN_CLOUD_ID" in exc.value.hint


async def test_live_tool_list_narrows_supported_operations_when_a_permission_group_is_disabled():
    mcp, t = make_mcp()
    assert mcp.supported_operations() == frozenset(OP_TOOLS)               # before connecting: the static map
    t.enabled -= {"searchJiraIssuesUsingJql", "listJiraBoards"}
    live = await mcp.connect()
    assert "jira.issue.search" not in live and "jira.agile.board.list" not in live and "jira.issue.get" in live
    assert mcp.supported_operations() == live


async def test_disabled_tool_raises_unsupported_before_sending_so_the_router_can_fall_back():
    mcp, t = make_mcp(enabled_tools=ALL_TOOLS - {"getJiraIssue"})
    with pytest.raises(UnsupportedOperationError) as exc:
        await mcp.jira.get_issue("PROJ-1")
    assert "permission group" in str(exc.value)
    assert "getJiraIssue" not in [c[0] for c in t.calls]
    client = ClientFactory().build_async(AtlassianConfig(site="x"), backends=[], preset="agent",
                                         extra_backends={BackendKind.MCP: mcp, BackendKind.REST: FakeBackend()})
    issue = await client.jira.issues.get("PROJ-1")
    assert issue.source.backend is BackendKind.REST                      # fell back
    assert (await client.via("rest").jira.issues.get("PROJ-1")).key == "PROJ-1"
    with pytest.raises(NoBackendAvailableError):
        await client.via("mcp").jira.issues.get("PROJ-1")


async def test_agent_preset_prefers_mcp_when_available():
    mcp, _ = make_mcp()
    client = ClientFactory().build_async(AtlassianConfig(site="x"), backends=[], preset="agent",
                                         extra_backends={BackendKind.MCP: mcp, BackendKind.REST: FakeBackend()})
    assert (await client.jira.issues.get("PROJ-1")).source.backend is BackendKind.MCP
    assert (await client.jira.jql.parse(["project = PROJ"]))[0].valid                    # JQL helpers are REST-only


async def test_error_mapping_from_tool_errors():
    mcp, t = make_mcp()
    with pytest.raises(NotFoundError):
        await mcp.jira.get_issue("PROJ-404")
    t.handlers["getJiraCurrentUser"] = lambda a: (_ for _ in ()).throw(__import__("atlassian_client.testing.mcp_fake", fromlist=["ToolError"]).ToolError("403 Forbidden"))
    with pytest.raises(PermissionDeniedError) as exc:
        await mcp.jira.myself()
    assert "permission group" in exc.value.hint
    r = McpToolResult(text="something odd happened", is_error=True)
    assert isinstance(mcp.jira._error(r, "x"), ServerError)
    assert isinstance(mcp.jira._error(McpToolResult(text="Resource not found"), "x"), NotFoundError)
    assert isinstance(mcp.jira._error(McpToolResult(text="you are not allowed"), "x"), PermissionDeniedError)


def test_result_payload_prefers_structured_then_json_text_then_text_and_unwraps_wrappers():
    assert McpToolResult(structured={"a": 1}, text="ignored").payload() == {"a": 1}
    assert McpToolResult(text='{"b": 2}').payload() == {"b": 2}
    assert McpToolResult(text="plain words").payload() == "plain words"
    assert McpToolResult(structured={"result": {"c": 3}}).payload() == {"c": 3}
    assert McpToolResult(text='{"data": [1, 2]}').payload() == [1, 2]
    assert McpToolResult(structured={"result": 1, "other": 2}).payload() == {"result": 1, "other": 2}


async def test_text_only_servers_work_because_json_text_is_parsed():
    mcp, t = make_mcp()
    orig = t.call_tool

    async def text_only(name, args):
        r = await orig(name, args)
        return McpToolResult(structured=None, text=r.text, is_error=r.is_error)

    t.call_tool = text_only
    assert (await mcp.jira.get_issue("PROJ-1")).summary == "Login fails"


async def test_health_ready_and_unavailable_and_aclose():
    mcp, t = make_mcp()
    h = await mcp.health()
    assert h.status == "ready" and "operations" in (h.detail or "")

    class Down(FakeMcpTransport):
        async def list_tools(self):
            from atlassian_client.core.errors import BackendUnavailableError
            raise BackendUnavailableError("connection refused")

    assert (await McpBackend(Down(), CFG).health()).status == "unavailable"
    await mcp.aclose()
    assert t.closed


def test_every_protocol_the_mcp_backend_claims_resolves_to_a_component():
    mcp, _ = make_mcp()
    from atlassian_client import protocols as P
    claimed = ["JiraIssueReader", "JiraIssueSearcher", "JiraProjectReader", "JiraMetadataReader", "JiraUserDirectory", "JiraAgileReader",
               "JsmReader", "ConfluenceContentReader", "ConfluenceSearcher", "ConfluenceSpaceReader", "ConfluenceTemplateReader",
               "TeamworkGraphReader", "SemanticSearcher", "IdentityReader"]
    for name in claimed:
        assert mcp.implementation_for(getattr(P, name)) is not None, name
    assert mcp.implementation_for(P.JiraJqlTools) is None and mcp.implementation_for(P.JiraIssueWriter) is None


async def test_identity_through_mcp():
    mcp, _ = make_mcp()
    from atlassian_client.protocols import IdentityReader
    me = await mcp.implementation_for(IdentityReader).whoami()
    assert me.account_id == "acc-ann" and me.auth_scheme == "mcp" and me.backend == "mcp"
    assert (await mcp.identity.accessible_resources())[0].id == "cloud-1"


def test_sdk_transport_is_lazy_and_never_logs_credentials():
    t = SdkMcpTransport("https://mcp.atlassian.com/v2/mcp", {"Authorization": "Basic SECRET"})
    assert t._session is None and "SECRET" not in repr(t.__dict__.get("url"))
