import httpx
import pytest

from atlassian_client import AtlassianConfig, BackendKind, ClientFactory
from atlassian_client import operations as ops
from atlassian_client.backends.rest_cloud import SUPPORTED, RestBackend, RestBackendFactory
from atlassian_client.capabilities import Deployment
from atlassian_client.core.auth import BasicTokenAuth, OAuth3LOAuth, ServiceAccountKeyAuth
from atlassian_client.core.services import CoreServices, build_cloud_auth
from atlassian_client.protocols import Backend
from atlassian_client.testing.server import ScenarioServer

BASIC = dict(site="example.atlassian.net", email="a@b.c", api_token="tok-123456")


def test_factory_metadata():
    f = RestBackendFactory()
    assert f.kind is BackendKind.REST and f.deployments == frozenset({Deployment.CLOUD}) and f.extra is None


@pytest.mark.parametrize("cfg,ok,fragment", [
    (dict(), False, "no Cloud credentials"),
    (dict(email="a@b.c", api_token="tok-123456"), False, "ATLASSIAN_SITE"),
    (BASIC, True, None),
    (dict(site="x.atlassian.net", service_account_key="key-123456"), True, None),
    (dict(cloud_id="cid", service_account_key="key-123456"), True, None),
    (dict(site="x.atlassian.net", oauth_client_id="i", oauth_client_secret="s", oauth_refresh_token="r"), True, None),
])
def test_availability_reasons(cfg, ok, fragment):
    a = RestBackendFactory().availability(AtlassianConfig(**cfg))
    assert a.ok is ok and (fragment is None or fragment in (a.reason or ""))


def test_auth_selection_priority():
    http = httpx.AsyncClient()
    assert isinstance(build_cloud_auth(AtlassianConfig(**BASIC), http), BasicTokenAuth)
    assert isinstance(build_cloud_auth(AtlassianConfig(**BASIC, service_account_key="k-123456"), http), ServiceAccountKeyAuth)
    assert isinstance(build_cloud_auth(AtlassianConfig(**BASIC, service_account_key="k-123456", oauth_client_id="i", oauth_client_secret="s",
                                                       oauth_refresh_token="r"), http), OAuth3LOAuth)
    assert build_cloud_auth(AtlassianConfig(), http) is None


def test_backend_satisfies_the_backend_protocol_and_reports_catalogued_operations():
    be = ClientFactory().backend(BackendKind.REST, AtlassianConfig(**BASIC))
    assert isinstance(be, Backend) and isinstance(be, RestBackend)
    assert be.supported_operations() == SUPPORTED and SUPPORTED <= set(ops.CATALOG)
    assert be.deployments == frozenset({Deployment.CLOUD})


def test_every_facade_operation_that_rest_serves_is_declared_supported():
    from atlassian_client.agent.describe import walk_namespaces
    from atlassian_client.testing import FakeBackend

    client = ClientFactory().build_async(AtlassianConfig(site="x"), backends=[], extra_backends={BackendKind.REST: FakeBackend()})
    facade_ops = {op for _, ns in walk_namespaces(client) for op in ns._OPS}
    mcp_only = {"confluence.space.instructions.get", "graph.context", "graph.object.get", "graph.search.semantic"}          # no REST API; served by the MCP backend
    assert facade_ops - mcp_only <= SUPPORTED, sorted(facade_ops - mcp_only - SUPPORTED)


def test_every_supported_operation_is_reachable_through_a_component():
    be = ClientFactory().backend(BackendKind.REST, AtlassianConfig(**BASIC))
    assert len(be._parts) == 6 and all(p.transport is be.transport for p in be._parts)


@pytest.mark.anyio
async def test_health_reports_ready_and_unavailable_without_raising():
    ok = ClientFactory().backend(BackendKind.REST, AtlassianConfig(**BASIC), http=ScenarioServer().client())
    assert (await ok.health()).status == "ready"
    srv = ScenarioServer()
    srv.fail_next = [(401, {})]
    bad = ClientFactory().backend(BackendKind.REST, AtlassianConfig(**BASIC), http=srv.client())
    h = await bad.health()
    assert h.status == "unavailable" and "AuthenticationError" in (h.detail or "")


@pytest.mark.anyio
async def test_aclose_closes_the_http_client():
    http = ScenarioServer().client()
    be = ClientFactory().backend(BackendKind.REST, AtlassianConfig(**BASIC), http=http)
    await be.aclose()
    assert http.is_closed
