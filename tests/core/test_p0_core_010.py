
import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from atlian_assiant.backends.rest_cloud.base import RestBase
from atlian_assiant.capabilities import BackendKind
from atlian_assiant.core.auth import BasicTokenAuth
from atlian_assiant.core.errors import InvalidRequestError
from atlian_assiant.core.http import HttpTransport, RequestSpec
from atlian_assiant.core.pagination import (
    STRATEGIES,
    CqlPagination,
    Cursor,
    JsmPagination,
    LinkCursorPagination,
    NextPageTokenPagination,
    OffsetPagination,
    OrgEventsCursorPagination,
)
from atlian_assiant.core.urls import ApiSurface, UrlResolver

pytestmark = [pytest.mark.layer_07]


def test_cursor_round_trip_and_garbage_rejected():
    c = Cursor(BackendKind.REST, "offset", {"start": 7})
    assert Cursor.decode(c.encode()) == c
    for bad in ("not-base64!!", "e30=", "eyJiIjoieCJ9"):
        with pytest.raises(InvalidRequestError) as exc:
            Cursor.decode(bad)
        assert "next_cursor" in exc.value.hint


def test_all_nine_strategies_registered():
    assert set(STRATEGIES) == {"offset", "next_page_token", "jsm", "link_cursor", "cql", "org_events", "dc_offset", "cli", "mcp"}


def resp(body, headers=None):
    return httpx.Response(200, json=body, headers=headers or {})


SPEC = RequestSpec("GET", ApiSurface.JIRA_PLATFORM, "/x")


def test_offset_stops_on_is_last_total_or_empty():
    s = OffsetPagination("values")
    assert s.parse(resp({"startAt": 0, "total": 5, "values": [1, 2]}), SPEC) == ([1, 2], {"start": 2}, 5)
    assert s.parse(resp({"startAt": 4, "total": 5, "values": [5]}), SPEC)[1] is None
    assert s.parse(resp({"startAt": 0, "isLast": True, "values": [1]}), SPEC)[1] is None
    assert s.parse(resp({"startAt": 0, "values": []}), SPEC)[1] is None


def test_next_page_token_roundtrip_in_body():
    s = NextPageTokenPagination()
    spec = RequestSpec("POST", ApiSurface.JIRA_PLATFORM, "/search/jql", json={"jql": "x"})
    first = s.first(spec, 10)
    assert first.json == {"jql": "x", "maxResults": 10}
    items, nxt, total = s.parse(resp({"issues": [{"id": 1}], "nextPageToken": "tok"}), first)
    assert nxt == {"token": "tok"} and total is None
    assert s.next(first, nxt).json["nextPageToken"] == "tok"
    assert s.parse(resp({"issues": [], "isLast": True}), first)[1] is None


def test_jsm_link_cql_and_org_events():
    assert JsmPagination().parse(resp({"start": 0, "size": 2, "isLastPage": False, "values": [1, 2]}), SPEC)[1] == {"start": 2}
    assert JsmPagination().parse(resp({"isLastPage": True, "values": [1]}), SPEC)[1] is None
    link = LinkCursorPagination()
    _, nxt, _ = link.parse(resp({"results": [1], "_links": {"next": "/wiki/api/v2/pages?cursor=abc&limit=2"}}), SPEC)
    nxt_spec = link.next(SPEC, nxt)
    assert nxt_spec.path == "/pages" and dict(nxt_spec.params) == {"cursor": "abc", "limit": "2"}        # cursor untouched
    assert link.parse(resp({"results": [1]}, {"Link": '</wiki/api/v2/pages?cursor=zz>; rel="next"'}), SPEC)[1] is not None
    assert CqlPagination().parse(resp({"results": [1], "start": 0, "_links": {"next": "x"}, "totalSize": 9}), SPEC) == ([1], {"start": 1}, 9)
    org = OrgEventsCursorPagination()
    assert org.parse(resp({"data": [], "meta": {"next": "c9"}}), SPEC)[1] == {"cursor": "c9"}      # empty page still yields a cursor
    assert org.first(SPEC, 9999).params["limit"] == 500


class Pager(RestBase):
    """RestBase.page against a synthetic server serving N items, to property-test every offset slice."""


def server(n_items: int):
    def handler(req: httpx.Request) -> httpx.Response:
        q = dict(req.url.params)
        start, size = int(q["startAt"]), int(q["maxResults"])
        return httpx.Response(200, json={"startAt": start, "maxResults": size, "total": n_items, "values": list(range(start, min(start + size, n_items)))})
    return handler


@settings(max_examples=60, deadline=None)
@given(n=st.integers(0, 40), limit=st.integers(1, 12))
@pytest.mark.anyio
async def test_offset_paging_has_no_gaps_no_duplicates_and_terminates(n, limit):
    http = httpx.AsyncClient(transport=httpx.MockTransport(server(n)))
    pager = Pager(HttpTransport(http, BasicTokenAuth("a", "b"), UrlResolver("x.atlassian.net", None, False)))
    got, cursor, pages = [], None, 0
    while True:
        page = await pager._page(RequestSpec("GET", ApiSurface.JIRA_PLATFORM, "/x"), OffsetPagination("values"), int, limit=limit, cursor=cursor)
        got += page.items
        pages += 1
        assert pages <= n + 2
        if not page.next_cursor:
            break
        cursor = page.next_cursor
    assert got == list(range(n))


@pytest.mark.anyio
async def test_cursor_from_another_operation_is_rejected():
    http = httpx.AsyncClient(transport=httpx.MockTransport(server(5)))
    pager = Pager(HttpTransport(http, BasicTokenAuth("a", "b"), UrlResolver("x.atlassian.net", None, False)))
    foreign = Cursor(BackendKind.REST, "jsm", {"start": 1}).encode()
    with pytest.raises(InvalidRequestError):
        await pager._page(RequestSpec("GET", ApiSurface.JIRA_PLATFORM, "/x"), OffsetPagination("values"), int, limit=2, cursor=foreign)
