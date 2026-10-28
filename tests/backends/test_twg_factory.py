"""TWG backend: factory availability, health, supported operations (design 10.5-10.7, 12.1)."""
import pytest

from atlian_assiant import AtlassianConfig, BackendKind
from atlian_assiant.backends.twg import TwgBackend, TwgBackendFactory
from atlian_assiant.capabilities import Deployment
from atlian_assiant.core.cli_runner import CliRunner
from atlian_assiant.core.errors import BackendUnavailableError
from atlian_assiant.factory import default_registry
from atlian_assiant.matrix import declared_operations
from atlian_assiant.testing.clisim import FakeCli
from atlian_assiant.testing.server import ScenarioServer


def test_registered_with_the_expected_metadata():
    f = default_registry().get(BackendKind.TWG)
    assert isinstance(f, TwgBackendFactory) and f.kind is BackendKind.TWG and Deployment.CLOUD in f.deployments and f.extra is None


def test_unavailable_reason_names_the_binary_and_the_setting_to_change():
    f = TwgBackendFactory(which=lambda b: None)
    a = f.availability(AtlassianConfig(twg_path="/nope/twg"))
    assert not a.ok and "/nope/twg" in a.reason and "ATLASSIAN_TWG_PATH" in a.reason and "login" in a.reason


def test_available_when_the_binary_resolves_and_create_returns_a_backend_of_this_kind():
    f = TwgBackendFactory(which=lambda b: "/usr/bin/" + b)
    assert f.availability(AtlassianConfig()).ok
    backend = f.create(AtlassianConfig(site="example.atlassian.net"), core=None)
    assert isinstance(backend, TwgBackend) and backend.kind is BackendKind.TWG and backend.supported_operations()


def test_create_without_the_binary_raises_backend_unavailable_and_sends_nothing():
    with pytest.raises(BackendUnavailableError) as exc:
        TwgBackendFactory(which=lambda b: None).create(AtlassianConfig(), core=None)
    assert "not installed" in str(exc.value) and exc.value.hint


@pytest.mark.anyio
async def test_health_probes_the_cli_and_never_raises():
    ok = TwgBackend(FakeCli(TwgBackend.cmap, ScenarioServer(), version="9.9.9").runner())
    health = await ok.health()
    assert health.status == "ready" and "9.9.9" in health.detail
    broken = TwgBackend(CliRunner("twg", which=lambda b: None))
    assert (await broken.health()).status == "unavailable"


def test_supported_operations_are_exactly_the_declared_matrix_cells():
    backend = TwgBackend(FakeCli(TwgBackend.cmap, ScenarioServer()).runner())
    assert backend.supported_operations() == declared_operations(BackendKind.TWG, TwgBackend.partial)
