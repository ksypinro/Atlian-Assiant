"""MX.rest.org-audit: the matrix cell 'Org audit events / events-stream' is served by the ORG_ADMIN backend through the client."""
import pytest

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.capabilities import Access
from atlian_assiant.core.errors import PolicyDeniedError
from atlian_assiant.policy import AccessPolicy
from atlian_assiant.testing.server import ScenarioServer

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]


def client(access):
    cfg = AtlassianConfig(site="example.atlassian.net", org_id="org-1", org_api_key="key-123456")
    be = ClientFactory().backend(BackendKind.ORG_ADMIN, cfg, http=ScenarioServer().client())
    return ClientFactory().build_async(cfg, backends=[], preset="service", policy=AccessPolicy(allowed=frozenset(access)),
                                       extra_backends={BackendKind.ORG_ADMIN: be})


async def test_org_audit_rest_requires_the_org_admin_access_class():
    c = client({Access.READ, Access.SEARCH})
    with pytest.raises(PolicyDeniedError):
        await c.admin.audit.events()
    ok = client({Access.READ, Access.ORG_ADMIN})
    page = await ok.admin.audit.events(limit=2)
    assert len(page.items) == 2 and page.items[0].source.backend is BackendKind.ORG_ADMIN
    assert "user_created" in await ok.admin.audit.actions() and (await ok.admin.audit.stream(limit=10)).next_cursor
    assert (await ok.admin.audit.event(page.items[0].id)).id == page.items[0].id
