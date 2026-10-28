"""ORG_ADMIN backend: factory availability, health, supported operations (design 10.11, 12.1)."""
import pytest

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.backends.org_admin import SUPPORTED, OrgAdminBackendFactory
from atlian_assiant.factory import default_registry
from atlian_assiant.protocols import OrgAuditReader
from atlian_assiant.protocols.base import implementation_of
from atlian_assiant.testing.server import ScenarioServer

CFG = dict(site="example.atlassian.net", org_id="org-1", org_api_key="key-123456")


def test_registered_in_the_default_registry_with_precise_unavailability_reasons():
    assert BackendKind.ORG_ADMIN in default_registry().factories()
    f = OrgAdminBackendFactory()
    assert f.availability(AtlassianConfig(site="x.atlassian.net")).reason == "ATLASSIAN_ORG_ID is not set"
    assert "ORG_API_KEY" in f.availability(AtlassianConfig(site="x.atlassian.net", org_id="o")).reason
    assert f.availability(AtlassianConfig(**CFG)).ok


def test_supported_operations_are_exactly_the_four_audit_operations():
    be = ClientFactory().backend(BackendKind.ORG_ADMIN, AtlassianConfig(**CFG), http=ScenarioServer().client())
    assert be.supported_operations() == SUPPORTED == {"admin.audit.list", "admin.audit.get", "admin.audit.actions", "admin.audit.stream"}
    assert implementation_of(be, OrgAuditReader) is not None


@pytest.mark.anyio
async def test_health_is_ready_and_never_raises_and_the_key_is_not_leaked():
    server = ScenarioServer()
    be = ClientFactory().backend(BackendKind.ORG_ADMIN, AtlassianConfig(**CFG), http=server.client())
    assert (await be.health()).status == "ready"
    assert server.requests[-1]["path"] == "/admin/v1/orgs/org-1/event-actions" and server.requests[-1]["headers"]["authorization"] == "Bearer key-123456"
    bad = ScenarioServer()
    bad.fail_next = [(401, {})]
    h = await ClientFactory().backend(BackendKind.ORG_ADMIN, AtlassianConfig(**CFG), http=bad.client()).health()
    assert h.status == "unavailable" and "key-123456" not in (h.detail or "")
