"""REST_DC backend: factory availability, health, supported operations, DC wire behaviour (design 10.2, 12.1)."""
import pytest

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant import operations as ops
from atlian_assiant.backends.rest_dc import RestDcBackend, RestDcBackendFactory, supported
from atlian_assiant.capabilities import Deployment
from atlian_assiant.core.errors import BackendUnavailableError, ConfigurationError, DeploymentMismatchError
from atlian_assiant.core.http import RequestSpec
from atlian_assiant.core.urls import ApiSurface
from atlian_assiant.factory import default_registry
from atlian_assiant.protocols import JiraIssueReader, JiraJqlTools
from atlian_assiant.testing.dc_server import DcScenarioServer

DC = dict(deployment=Deployment.DATA_CENTER, jira_dc_base_url="https://jira.corp.example", confluence_dc_base_url="https://wiki.corp.example", pat="pat-123456789")


def test_registered_and_data_center_only():
    f = default_registry().get(BackendKind.REST_DC)
    assert isinstance(f, RestDcBackendFactory) and f.deployments == frozenset({Deployment.DATA_CENTER}) and f.extra is None


@pytest.mark.parametrize("cfg,fragment", [
    (dict(), "deployment is Cloud"),
    (dict(deployment=Deployment.DATA_CENTER), "DC_BASE_URL"),
    (dict(deployment=Deployment.DATA_CENTER, jira_dc_base_url="https://j.example"), "no Data Center credentials"),
])
def test_availability_reasons_are_precise(cfg, fragment):
    a = RestDcBackendFactory().availability(AtlassianConfig(**cfg))
    assert not a.ok and fragment in a.reason


def test_pat_or_discouraged_basic_makes_it_available():
    f = RestDcBackendFactory()
    assert f.availability(AtlassianConfig(**DC)).ok
    basic = {k: v for k, v in DC.items() if k != "pat"} | dict(dc_basic_user="svc", dc_basic_password="pw-123456")
    assert f.availability(AtlassianConfig(**basic)).ok


def test_unavailable_backend_raises_before_anything_is_sent():
    server = DcScenarioServer()
    with pytest.raises(BackendUnavailableError):
        ClientFactory().backend(BackendKind.REST_DC, AtlassianConfig(), http=server.client())
    assert not server.dc_requests


def test_supported_operations_follow_the_matrix_and_stay_catalog_operations():
    backend = ClientFactory().backend(BackendKind.REST_DC, AtlassianConfig(**DC), http=DcScenarioServer().client())
    assert isinstance(backend, RestDcBackend) and backend.supported_operations() == supported()
    assert supported() <= set(ops.CATALOG)
    assert "jira.jql.parse" not in supported() and "jira.admin.screen.get" not in supported() and "confluence.content.archive" not in supported()
    assert {"webhook.jira.admin.register", "webhook.confluence_dc.register", "export.jira.csv"} <= supported()


@pytest.mark.anyio
async def test_health_reports_ready_and_unavailable_without_raising():
    server = DcScenarioServer()
    backend = ClientFactory().backend(BackendKind.REST_DC, AtlassianConfig(**DC), http=server.client())
    assert (await backend.health()).status == "ready"
    server.fail_next = [(500, {})] * 8
    assert (await backend.health()).status == "unavailable"


@pytest.mark.anyio
async def test_pat_is_a_bearer_token_sent_only_to_the_configured_data_center_hosts():
    server = DcScenarioServer()
    backend = ClientFactory().backend(BackendKind.REST_DC, AtlassianConfig(**DC), http=server.client())
    await backend.implementation_for(JiraIssueReader).get_issue("PROJ-1")
    assert server.dc_requests and all(r["headers"]["authorization"] == "Bearer pat-123456789" for r in server.dc_requests)


@pytest.mark.anyio
async def test_confluence_v2_does_not_exist_on_data_center_and_a_cloud_surface_never_receives_the_pat():
    server = DcScenarioServer()
    backend = ClientFactory().backend(BackendKind.REST_DC, AtlassianConfig(**DC), http=server.client())
    with pytest.raises(ConfigurationError):
        await backend.transport.send(RequestSpec("GET", ApiSurface.CONFLUENCE_V2, "/pages/1", operation="confluence.content.get"))
    with pytest.raises(ConfigurationError):                                               # foreign host through an absolute URL
        await backend.transport.send(RequestSpec("GET", ApiSurface.JIRA_PLATFORM, "https://evil.example/steal", operation="jira.issue.get"))
    assert not server.dc_requests


@pytest.mark.anyio
async def test_cloud_only_jql_tools_say_so_instead_of_pretending():
    backend = ClientFactory().backend(BackendKind.REST_DC, AtlassianConfig(**DC), http=DcScenarioServer().client())
    with pytest.raises(DeploymentMismatchError):
        await backend.implementation_for(JiraJqlTools).evaluate_expression("1", {})


@pytest.mark.anyio
async def test_the_data_center_preset_builds_a_client_that_reads_through_rest_dc_only():
    from atlian_assiant import ClientFactory

    server = DcScenarioServer()
    client = ClientFactory().build_async(AtlassianConfig(**DC), preset="data_center", http=server.client())
    assert BackendKind.REST_DC in client.router.backends and BackendKind.REST not in client.router.backends
    issue = await client.jira.issues.get("PROJ-1")
    assert issue.key == "PROJ-1" and issue.source.backend is BackendKind.REST_DC
    assert all(r["headers"]["authorization"].startswith("Bearer ") for r in server.dc_requests)
    assert "rest_dc" in client.describe()["backends"]
