from __future__ import annotations

from typing import Any

from atlassian_client import AtlassianConfig, BackendKind
from atlassian_client.backends.mcp import McpBackend
from atlassian_client.protocols.base import implementation_of
from atlassian_client.testing import FakeBackend
from atlassian_client.testing.mcp_fake import FakeMcpTransport

CFG = AtlassianConfig(site="example.atlassian.net", email="ann@example.com", api_token="tok-123456")


def make_mcp(**kw: Any) -> tuple[McpBackend, FakeMcpTransport]:
    transport = FakeMcpTransport(**kw)
    return McpBackend(transport, CFG), transport


def dump(x: Any) -> Any:
    if isinstance(x, list):
        return [dump(i) for i in x]
    if hasattr(x, "items") and hasattr(x, "next_cursor"):
        return {"items": dump(x.items), "more": x.has_more}
    if hasattr(x, "model_dump"):
        return x.model_dump(exclude={"source"}, mode="json")
    return x


async def same(proto: type, method: str, *args: Any, **kwargs: Any) -> Any:
    """Call the same method on the MCP backend and the Fake and require identical normalized output."""
    mcp, _ = make_mcp()
    fake = FakeBackend(BackendKind.MCP)
    got = dump(await getattr(implementation_of(mcp, proto), method)(*args, **kwargs))
    want = dump(await getattr(implementation_of(fake, proto), method)(*args, **kwargs))
    assert got == want, f"{proto.__name__}.{method} differs between MCP and the reference data:\n{got}\n!=\n{want}"
    return got
