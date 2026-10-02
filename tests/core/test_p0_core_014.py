import httpx
import pytest

from atlassian_client import AtlassianConfig, BackendKind, ClientFactory, Preset
from atlassian_client.capabilities import Deployment
from atlassian_client.core.errors import BackendUnavailableError
from atlassian_client.factory import Availability, BackendRegistry, default_registry
from atlassian_client.protocols import JiraIssueReader
from atlassian_client.testing import FakeBackend
from atlassian_client.testing.server import ScenarioServer

CFG = dict(site="example.atlassian.net", email="a@b.c", api_token="tok-123456")


class Dummy:
    kind = BackendKind.MCP
    deployments = frozenset({Deployment.CLOUD})
    extra = "mcp"

    def availability(self, config):
        return Availability(False, "pip install 'atlassian-client[mcp]'")

    def create(self, config, core):
        raise AssertionError("must not be built when unavailable")


def test_registry_rejects_duplicates_unless_replace():
    reg = BackendRegistry()
    reg.register(Dummy())
    with pytest.raises(ValueError):
        reg.register(Dummy())
    reg.register(Dummy(), replace=True)
    assert BackendKind.MCP in reg.factories()
    with pytest.raises(BackendUnavailableError):
        reg.get(BackendKind.TWG)


def test_default_registry_has_rest():
    assert BackendKind.REST in default_registry().factories()


def test_unavailable_backends_are_skipped_with_a_reason():
    reg = default_registry()
    reg.register(Dummy(), replace=True)
    client = ClientFactory(reg).build_async(AtlassianConfig(**CFG), http=ScenarioServer().client())
    d = client.describe()
    assert d["backends"]["rest"].startswith("ready") and d["backends"]["mcp"] == "unavailable: pip install 'atlassian-client[mcp]'"


def test_no_credentials_means_rest_unavailable_with_actionable_reason():
    client = ClientFactory().build_async(AtlassianConfig(site="x.atlassian.net"))
    assert "ATLASSIAN_EMAIL" in client.describe()["backends"]["rest"]
    assert any("No backend is available" in n for n in client.describe()["notes"])


def test_missing_site_is_reported():
    client = ClientFactory().build_async(AtlassianConfig(email="a@b.c", api_token="tok-123456"))
    assert "ATLASSIAN_SITE" in client.describe()["backends"]["rest"]


def test_enabled_backends_filter_and_deployment_filter():
    f = ClientFactory()
    c = f.build_async(AtlassianConfig(**CFG), backends=[BackendKind.MCP])
    assert c.describe()["backends"]["rest"] == "unavailable: not enabled (enabled_backends)"
    dc = f.build_async(AtlassianConfig(**CFG, deployment=Deployment.DATA_CENTER))
    assert "not available on data_center" in dc.describe()["backends"]["rest"] and dc.preset is Preset.DATA_CENTER


@pytest.mark.anyio
async def test_backend_and_sub_client_return_the_right_objects():
    f = ClientFactory()
    http = ScenarioServer().client()
    reader = f.sub_client(JiraIssueReader, backend=BackendKind.REST, config=AtlassianConfig(**CFG), http=http)
    assert (await reader.get_issue("PROJ-1")).key == "PROJ-1"
    with pytest.raises(BackendUnavailableError):
        f.backend(BackendKind.REST, AtlassianConfig())
    with pytest.raises(TypeError):
        class NoIssue:
            kind = BackendKind.REST
        f.sub_client(JiraIssueReader, backend=BackendKind.REST, config=AtlassianConfig(**CFG), http=http).__class__  # sanity
        reg = BackendRegistry()

        class F2(Dummy):
            kind = BackendKind.REST

            def availability(self, config):
                return Availability(True)

            def create(self, config, core):
                return NoIssue()

        reg.register(F2())
        ClientFactory(reg).sub_client(JiraIssueReader, backend=BackendKind.REST, config=AtlassianConfig(**CFG))


def test_presets_are_wired():
    for ctor, preset in ((ClientFactory.for_service, Preset.SERVICE), (ClientFactory.for_shell, Preset.SHELL),
                         (ClientFactory.for_analytics, Preset.ANALYTICS)):
        assert ctor(AtlassianConfig(**CFG)).preset is preset
    agent = ClientFactory.for_agent(AtlassianConfig(**CFG))
    assert agent.preset is Preset.AGENT and [a.value for a in agent.policy.allowed] in (["read", "search"], ["search", "read"])
    assert ClientFactory.for_data_center(AtlassianConfig(**CFG)).preset is Preset.DATA_CENTER


def test_extra_backends_can_be_injected():
    c = ClientFactory().build_async(AtlassianConfig(site="x"), extra_backends={BackendKind.REST: FakeBackend()})
    assert c.describe()["backends"]["rest"].startswith("ready")
