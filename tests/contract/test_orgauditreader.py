"""OrgAuditReader contract: runs against the fake and, over HTTP, against the ORG_ADMIN backend ("rest" = wire level)."""
import pytest

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.core.errors import InvalidRequestError, NotFoundError
from atlian_assiant.protocols import OrgAuditReader
from atlian_assiant.protocols.base import implementation_of
from atlian_assiant.testing import FakeBackend
from atlian_assiant.testing.server import ScenarioServer

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]


class H:
    def __init__(self, name, backend, server):
        self.name, self.backend, self.server = name, backend, server

    @property
    def reader(self) -> OrgAuditReader:
        return implementation_of(self.backend, OrgAuditReader)


@pytest.fixture(params=["fake", "rest"])
def audit(request):
    if request.param == "fake":
        return H("fake", FakeBackend(), None)
    server = ScenarioServer()
    cfg = AtlassianConfig(site="example.atlassian.net", org_id="org-1", org_api_key="key-123456")
    return H("rest", ClientFactory().backend(BackendKind.ORG_ADMIN, cfg, http=server.client()), server)


async def test_org_audit_events_page_with_opaque_cursors(audit):
    r = audit.reader
    first = await r.list_events(limit=2)
    assert len(first.items) == 2 and first.next_cursor and first.items[0].id
    ids, cursor = [e.id for e in first.items], first.next_cursor
    for _ in range(10):                                                   # follow the cursor to the end: no repeats, no gaps
        page = await r.list_events(limit=2, cursor=cursor)
        ids += [e.id for e in page.items]
        cursor = page.next_cursor
        if cursor is None:
            break
    assert cursor is None and len(ids) == len(set(ids)) >= 3


async def test_org_audit_get_event_actions_and_missing_event(audit):
    r = audit.reader
    first = (await r.list_events(limit=1)).items[0]
    assert (await r.get_event(first.id)).id == first.id
    assert "user_created" in await r.list_event_actions()
    with pytest.raises(NotFoundError):
        await r.get_event("nope")


async def test_org_audit_stream_returns_a_cursor_even_on_the_last_page(audit):
    r = audit.reader
    page = await r.poll_stream(limit=500)
    assert len(page.events) >= 3 and page.next_cursor
    again = await r.poll_stream(cursor=page.next_cursor, limit=500)
    assert again.events == [] and again.next_cursor                      # nothing new, but the checkpoint survives


async def test_org_audit_rest_maps_fields_filters_and_validates_limits(audit):
    if audit.server is None:
        pytest.skip("wire-level assertion")
    r = audit.reader
    ev = (await r.list_events(action="login", limit=10)).items
    assert {e.action for e in ev} == {"login"} and ev[0].actor_id == "acc-ann" and ev[0].occurred_at and ev[0].processed_at
    assert audit.server.requests[-1]["query"]["action"] == "login"
    await r.list_events(limit=9999)
    assert audit.server.requests[-1]["query"]["limit"] == "500"             # clamped to the documented maximum
    with pytest.raises(InvalidRequestError):
        await r.list_events(limit=0)
    page = await r.list_events(limit=1)
    with pytest.raises(InvalidRequestError, match="cursor does not belong"):
        await r.poll_stream(cursor=page.next_cursor)                         # a list cursor is not a stream cursor
    await r.poll_stream(limit=5, sort_order="desc")
    assert audit.server.requests[-1]["query"]["sortOrder"] == "desc"
