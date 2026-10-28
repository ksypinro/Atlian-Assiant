"""MCP transport abstraction (design section 10.3).

``McpTransport`` is the only thing that touches the network; tests inject a fake. ``SdkMcpTransport`` uses the official
``mcp`` SDK (streamable HTTP) with Basic / Bearer headers. OAuth 2.1 (browser consent) is delegated to the SDK's auth support
and is NOT wired here yet.

NOTE (design section 22, item 8): tool argument names and result shapes of the live Atlassian MCP server have not been
verified against a real tenant. They are isolated in ``backend.py`` so correcting them is a data change.
"""
from __future__ import annotations

import json
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ...core.errors import BackendUnavailableError


@dataclass
class McpToolResult:
    """Normalized result of one tool call: structured JSON when offered, else parsed text, else raw text."""

    structured: Any | None = None
    text: str = ""
    is_error: bool = False
    raw: Any = field(default=None, repr=False)

    def payload(self) -> Any:
        """The best available data: structured content, else JSON parsed from text, else the raw text."""
        if self.structured is not None:
            data = self.structured
        else:
            try:
                data = json.loads(self.text)
            except (ValueError, TypeError):
                try:                                  # some v2 tools print a JSON object followed by a plain-text note
                    data = json.JSONDecoder().raw_decode(self.text.lstrip())[0]
                except (ValueError, TypeError):
                    return self.text
        # tolerate common single-key wrappers
        if isinstance(data, dict) and len(data) == 1 and next(iter(data)) in ("result", "data"):
            return data[next(iter(data))]
        return data


@runtime_checkable
class McpTransport(Protocol):
    async def list_tools(self) -> list[str]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpToolResult: ...

    async def aclose(self) -> None: ...


class SdkMcpTransport:
    """Official ``mcp`` SDK over streamable HTTP. Connects lazily on first use."""

    def __init__(self, url: str, headers: dict[str, str]) -> None:
        self.url, self.headers = url, headers
        self._stack: AsyncExitStack | None = None
        self._session: Any = None

    async def _connect(self) -> Any:
        if self._session is not None:
            return self._session
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import (  # type: ignore[attr-defined,unused-ignore]
                create_mcp_http_client,
                streamable_http_client,
            )
        except ImportError as exc:                                   # pragma: no cover - depends on the optional extra
            raise BackendUnavailableError("the 'mcp' package is not installed", hint="pip install 'atlian-assiant[mcp]'") from exc
        stack = AsyncExitStack()
        try:
            http = create_mcp_http_client(headers=self.headers)
            streams = await stack.enter_async_context(streamable_http_client(self.url, http_client=http))
            session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
            await session.initialize()
        except Exception as exc:
            await stack.aclose()
            raise BackendUnavailableError(f"could not connect to the MCP server: {type(exc).__name__}: {exc}",
                                          hint="Check ATLASSIAN_MCP_URL and that API-token auth is enabled by your org admin.") from exc
        self._stack, self._session = stack, session
        return session

    async def list_tools(self) -> list[str]:
        session = await self._connect()
        result = await session.list_tools()
        return [t.name for t in result.tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpToolResult:
        session = await self._connect()
        res = await session.call_tool(name, arguments)
        text = "\n".join(getattr(c, "text", "") for c in (res.content or []) if getattr(c, "type", "") == "text")
        return McpToolResult(structured=getattr(res, "structuredContent", None), text=text, is_error=bool(getattr(res, "isError", False)), raw=res)

    async def aclose(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = self._session = None
