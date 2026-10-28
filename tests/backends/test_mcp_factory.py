import pytest

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.backends.mcp import McpBackend, McpBackendFactory
from atlian_assiant.capabilities import Deployment
from atlian_assiant.factory import default_registry
from atlian_assiant.protocols import Backend
from atlian_assiant.testing.mcp_fake import FakeMcpTransport

pytestmark = [pytest.mark.layer_04]
BASIC = dict(site="example.atlassian.net", email="a@b.c", api_token="tok-123456")


def test_registered_by_default_with_metadata():
    f = default_registry().factories()[BackendKind.MCP]
    assert f.kind is BackendKind.MCP and f.deployments == frozenset({Deployment.CLOUD}) and f.extra == "mcp"


@pytest.mark.parametrize("cfg,ok,fragment", [
    (dict(), False, "ATLASSIAN_EMAIL"), (BASIC, True, None),
    (dict(site="x", service_account_key="key-123456"), True, None),
])
def test_availability(cfg, ok, fragment):
    a = McpBackendFactory().availability(AtlassianConfig(**cfg))
    assert a.ok is ok and (fragment is None or fragment in (a.reason or ""))


def test_create_builds_an_sdk_transport_with_the_right_headers_and_flat_tools_flag():
    be = McpBackendFactory().create(AtlassianConfig(**BASIC, mcp_flat_tools=True), None)  # type: ignore[arg-type]
    assert isinstance(be, McpBackend) and isinstance(be, Backend)
    assert be.transport.url.endswith("/v2/mcp?tools=all") and be.transport.headers["Authorization"].startswith("Basic ")
    key = McpBackendFactory().create(AtlassianConfig(site="x", service_account_key="key-123456"), None)  # type: ignore[arg-type]
    assert key.transport.headers["Authorization"] == "Bearer key-123456"


def test_missing_sdk_is_reported_with_the_pip_extra(monkeypatch):
    import builtins
    real = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "mcp":
            raise ImportError("no mcp")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    a = McpBackendFactory().availability(AtlassianConfig(**BASIC))
    assert not a.ok and "pip install 'atlian-assiant[mcp]'" in a.reason


def test_injected_transport_factory_builds_a_working_client_end_to_end():
    reg = default_registry()
    reg.register(McpBackendFactory(lambda cfg: FakeMcpTransport()), replace=True)
    client = ClientFactory(reg).build_async(AtlassianConfig(**BASIC), preset="agent")
    assert client.describe()["backends"]["mcp"].startswith("ready")
    import asyncio
    issue = asyncio.run(client.jira.issues.get("PROJ-1"))
    assert issue.source.backend is BackendKind.MCP
