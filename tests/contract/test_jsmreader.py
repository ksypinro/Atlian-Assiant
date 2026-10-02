import pytest

from atlassian_client.core.errors import NotFoundError
from atlassian_client.protocols import JsmReader

pytestmark = [pytest.mark.layer_01]


@pytest.mark.anyio
async def test_jsm_read_service_desks_request_types_requests(impl):
    r = impl(JsmReader)
    assert (await r.info()).version == "1001.0.0"
    assert (await r.list_service_desks()).items[0].project_key == "OPS"
    assert (await r.list_request_types("4")).items[0].name == "Get IT help"
    req = (await r.list_requests(service_desk_id="4")).items[0]
    assert (req.issue_key, req.summary, req.status) == ("OPS-1", "VPN down", "Open")
    assert (await r.get_request("OPS-1")).description == "Cannot connect"


@pytest.mark.anyio
async def test_jsm_read_comments_public_only_sla_kb_orgs_customers(impl):
    r = impl(JsmReader)
    assert len((await r.list_request_comments("OPS-1")).items) == 2
    public = (await r.list_request_comments("OPS-1", public_only=True)).items
    assert [c.id for c in public] == ["rc1"] and all(c.public for c in public)
    sla = (await r.get_request_sla("OPS-1"))[0]
    assert (sla.name, sla.breached, sla.remaining_millis) == ("Time to resolution", False, 3600000)
    assert (await r.search_knowledge_base("vpn"))[0].title == "Fix VPN"
    assert (await r.list_organizations()).items[0].name == "Acme"
    assert (await r.list_customers("4")).items[0].display_name == "Bob Ray"


@pytest.mark.anyio
async def test_jsm_read_unknown_request_is_not_found(impl):
    with pytest.raises(NotFoundError):
        await impl(JsmReader).get_request("OPS-999")
