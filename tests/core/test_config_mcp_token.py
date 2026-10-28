"""ATLASSIAN_MCP_AUTH names a mode; a pasted token there must fail loudly without echoing it. MCP may use its own scoped token."""
from __future__ import annotations

import pytest

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.core.errors import ConfigurationError


def test_a_token_pasted_into_mcp_auth_is_rejected_without_echoing_it():
    with pytest.raises(ConfigurationError) as exc:
        AtlassianConfig(mcp_auth="pasted-secret-token-value-0123456789")
    assert "pasted-secret" not in str(exc.value) and "ATLASSIAN_MCP_API_TOKEN" in (exc.value.hint or "")


def test_mcp_uses_its_own_scoped_token_when_set():
    import base64

    seen = {}

    class Transport:
        def __init__(self, url, headers):
            seen.update(headers)

    from atlian_assiant.backends.mcp import backend as mcp

    original = mcp.SdkMcpTransport
    mcp.SdkMcpTransport = Transport  # type: ignore[misc,assignment]
    try:
        cfg = AtlassianConfig(site="x.atlassian.net", email="a@b.c", api_token="classic-token", mcp_api_token="scoped-token")
        ClientFactory().backend(BackendKind.MCP, cfg)
    finally:
        mcp.SdkMcpTransport = original  # type: ignore[misc]
    assert base64.b64decode(seen["Authorization"].split()[1]).decode() == "a@b.c:scoped-token"
