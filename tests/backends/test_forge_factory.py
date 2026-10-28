"""FORGE backend: factory availability, health, supported operations (design 10.10, 12.1)."""
import pytest

from atlian_assiant import AtlassianConfig, BackendKind
from atlian_assiant.backends.forge import ForgeAppRestClient, ForgeBackend, ForgeBackendFactory
from atlian_assiant.factory import default_registry


def test_registered_and_precise_about_why_it_is_unavailable():
    assert BackendKind.FORGE in default_registry().factories()
    f = ForgeBackendFactory()
    assert "Preview" in f.availability(AtlassianConfig(site="x")).reason
    assert "FORGE_APP_ID" in f.availability(AtlassianConfig(site="x", preview_features_allowed=True)).reason
    assert "OAuth" in f.availability(AtlassianConfig(site="x", preview_features_allowed=True, forge_app_id="a", forge_env_id="e")).reason
    ok = AtlassianConfig(site="x", preview_features_allowed=True, forge_app_id="a", forge_env_id="e", oauth_client_id="c", oauth_refresh_token="r")
    assert f.availability(ok).ok


def test_supported_operations_is_empty_because_forge_feeds_event_sources_not_catalog_operations():
    class Dummy(ForgeAppRestClient):
        def __init__(self):
            pass
    be = ForgeBackend(Dummy())
    assert be.supported_operations() == frozenset() and be.implementation_for(object) is None


@pytest.mark.anyio
async def test_health_is_degraded_because_the_api_is_preview_and_never_raises():
    class Ok:
        async def health(self):
            return {"ok": True}

    class Broken:
        async def health(self):
            raise RuntimeError("boom")
    assert (await ForgeBackend(Ok()).health()).status == "degraded" and "Preview" in (await ForgeBackend(Ok()).health()).detail
    assert (await ForgeBackend(Broken()).health()).status == "unavailable"
