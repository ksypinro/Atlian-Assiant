"""Live-observed MCP failures that arrive as successful calls (text error envelopes / sentences)."""

from __future__ import annotations

import pytest

from atlian_assiant import AtlassianConfig
from atlian_assiant.backends.mcp.backend import McpClient
from atlian_assiant.backends.mcp.session import McpToolResult
from atlian_assiant.core.errors import AuthenticationError, PermissionDeniedError

pytestmark = pytest.mark.anyio


class _T:
    def __init__(self, text: str) -> None:
        self.text = text

    async def list_tools(self) -> list[str]:
        return ["getJiraIssue"]

    async def call_tool(self, name: str, arguments: dict) -> McpToolResult:
        return McpToolResult(text=self.text)

    async def aclose(self) -> None:
        pass


def _client(text: str) -> McpClient:
    return McpClient(_T(text), AtlassianConfig(site="x.atlassian.net", cloud_id="abc"))


async def test_ungranted_cloud_id_sentence_is_permission_denied() -> None:
    with pytest.raises(PermissionDeniedError, match="explicitly granted") as e:
        await _client("Cloud id: abc isn't explicitly granted by the user.").raw("getJiraIssue", {}, op="jira.issue.get")
    assert "scoped" in (e.value.hint or "")


async def test_error_envelope_with_missing_scope_is_authentication_error() -> None:
    body = '{"error":true,"message":"The session token is missing the scope claim required","statusCode":401}'
    with pytest.raises(AuthenticationError):
        await _client(body).raw("getJiraIssue", {}, op="jira.issue.get")


async def test_normal_json_payload_passes_through() -> None:
    assert await _client('{"key":"KAN-1"}').raw("getJiraIssue", {}, op="jira.issue.get") == {"key": "KAN-1"}


async def test_api_token_auth_disabled_by_the_org_names_the_admin_setting() -> None:
    body = '{"error":true,"message":"You don\'t have permission to connect via API token. Please ask your organization admin for access."}'
    with pytest.raises(PermissionDeniedError) as e:
        await _client(body).raw("getJiraIssue", {}, op="jira.issue.get")
    assert "Rovo MCP server" in (e.value.hint or "")
