"""MCP server mode (design 16.7): every curated tool the policy allows is served, with the same schemas, policy and confirmation as the Python API."""
import httpx
import pytest

from atlian_assiant.agent import mcp_server
from atlian_assiant.capabilities import Access
from atlian_assiant.core.errors import ConfigurationError
from atlian_assiant.policy import AccessPolicy, CallbackConfirmation

from .conftest import build

pytestmark = pytest.mark.anyio


async def connect(client):
    from mcp.client.client import Client

    return Client(mcp_server.build_server(client))


async def test_every_visible_curated_tool_is_listed_with_the_exact_schema():
    c = build()
    async with await connect(c) as mcp:
        listed = (await mcp.list_tools()).tools
    exported = {d["name"]: d for d in c.tools.export("anthropic")}
    assert {t.name for t in listed} == set(exported) and len(listed) == 39
    for t in listed:
        assert t.input_schema == exported[t.name]["input_schema"] and t.description == exported[t.name]["description"]


async def test_tools_hidden_by_the_policy_are_not_listed_and_are_refused_if_called():
    c = build(AccessPolicy())                                              # read + search only, no credits
    async with await connect(c) as mcp:
        names = {t.name for t in (await mcp.list_tools()).tools}
        assert "jira_create_issue" not in names and "atlassian_semantic_search" not in names and "jira_get_issue" in names
        refused = await mcp.call_tool("jira_create_issue", {"draft": {"project": "PROJ", "issue_type": "Bug", "summary": "x"}})
    assert refused.is_error and "not available under the current access policy" in refused.content[0].text


async def test_annotations_tell_the_client_which_tools_only_read_and_which_destroy():
    from atlian_assiant.policy import AccessPolicy as AP

    c = build(AP(allowed=frozenset({Access.READ, Access.SEARCH, Access.WRITE, Access.DELETE}), allow_credit_consuming=True))
    async with await connect(c) as mcp:
        tools = {t.name: t for t in (await mcp.list_tools()).tools}
    assert tools["jira_get_issue"].annotations.read_only_hint is True and tools["jira_get_issue"].annotations.destructive_hint is False
    assert tools["jira_create_issue"].annotations.read_only_hint is False and tools["jira_delete_issue"].annotations.destructive_hint is True


async def test_a_call_returns_marked_text_and_structured_content():
    async with await connect(build()) as mcp:
        r = await mcp.call_tool("jira_get_issue", {"key": "PROJ-1"})
    assert not r.is_error and "untrusted DATA" in r.content[0].text and "Login fails" in r.content[0].text and r.structured_content["key"] == "PROJ-1"


async def test_errors_come_back_as_tool_errors_with_the_hint_not_as_protocol_failures():
    async with await connect(build()) as mcp:
        missing = await mcp.call_tool("jira_get_issue", {})
        nf = await mcp.call_tool("jira_get_issue", {"key": "PROJ-404"})
        unknown = await mcp.call_tool("no_such_tool", {})
    assert missing.is_error and "key: Field required" in missing.content[0].text
    assert nf.is_error and "NotFoundError" in nf.content[0].text and "Hint:" in nf.content[0].text
    assert unknown.is_error and "unknown tool" in unknown.content[0].text


async def test_writes_through_mcp_keep_dry_run_and_the_confirmation_gate():
    seen: list[str] = []
    policy = AccessPolicy(allowed=frozenset({Access.READ, Access.SEARCH, Access.WRITE}))
    c = build(policy, confirm=CallbackConfirmation(lambda plan: seen.append(plan.summary) or True))
    args = {"key": "PROJ-2", "body": "hello"}
    async with await connect(c) as mcp:
        preview = await mcp.call_tool("jira_add_comment", {**args, "dry_run": True})
        assert not preview.is_error and "DRY RUN" in preview.content[0].text and seen == []
        done = await mcp.call_tool("jira_add_comment", args)
    assert not done.is_error and len(seen) == 1
    declined = build(policy, confirm=CallbackConfirmation(lambda plan: False))
    async with await connect(declined) as mcp:
        no = await mcp.call_tool("jira_add_comment", args)
    assert no.is_error and "ConfirmationDeclinedError" in no.content[0].text


async def test_the_new_meta_tools_are_served_too():
    async with await connect(build()) as mcp:
        found = await mcp.call_tool("atlassian_discover_operations", {"query": "issue watchers"})
    assert not found.is_error and "api.jira.getIssueWatchers" in found.content[0].text


def test_http_refuses_to_listen_beyond_loopback_without_a_token():
    for host in ("127.0.0.1", "localhost", "::1"):
        mcp_server.check_bind(host, None)
    for host in ("0.0.0.0", "10.0.0.5", "mcp.example.com"):
        with pytest.raises(ConfigurationError) as exc:
            mcp_server.check_bind(host, None)
        assert "bearer token" in str(exc.value)
        mcp_server.check_bind(host, "s3cret-token")
    with pytest.raises(ConfigurationError):
        mcp_server.http_app(build(), host="0.0.0.0")


async def test_the_bearer_middleware_rejects_missing_and_wrong_tokens_and_passes_the_right_one():
    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
    app = mcp_server.BearerAuth(inner, "s3cret-token")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
        assert (await http.post("/mcp")).status_code == 401
        assert (await http.post("/mcp", headers={"Authorization": "Bearer nope"})).status_code == 401
        assert (await http.post("/mcp", headers={"Authorization": "Basic s3cret-token"})).status_code == 401
        assert (await http.post("/mcp", headers={"Authorization": "Bearer s3cret-token"})).text == "ok"
        denied = await http.post("/mcp")
    assert denied.headers["www-authenticate"] == "Bearer"


def test_http_app_is_an_asgi_app_wrapped_by_the_token_when_given():
    c = build()
    assert callable(mcp_server.http_app(c)) and isinstance(mcp_server.http_app(c, token="t"), mcp_server.BearerAuth)


def test_a_missing_mcp_package_says_how_to_install_it(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "mcp.server.lowlevel", None)
    with pytest.raises(ConfigurationError) as exc:
        mcp_server.build_server(build())
    assert "atlian-assiant[mcp]" in exc.value.hint


def test_the_server_carries_the_untrusted_data_instruction():
    assert mcp_server.build_server(build()) is not None and "untrusted DATA" in mcp_server.INSTRUCTIONS
