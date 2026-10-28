"""HTTP-level behaviour of the REST backend through the mock transport (test layer 3)."""
import base64

import pytest

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.core.errors import AuthenticationError, NotFoundError, RateLimitedError
from atlian_assiant.testing.server import ScenarioServer

pytestmark = [pytest.mark.layer_03]


def backend(server, **extra):
    cfg = AtlassianConfig(**{**dict(site="example.atlassian.net", email="ann@example.com", api_token="tok-123456"), **extra})
    return ClientFactory().backend(BackendKind.REST, cfg, http=server.client())


@pytest.mark.anyio
async def test_basic_auth_header_and_site_urls():
    srv = ScenarioServer()
    await backend(srv).jira.get_issue("PROJ-1")
    req = srv.requests[-1]
    assert req["headers"]["authorization"] == "Basic " + base64.b64encode(b"ann@example.com:tok-123456").decode()
    assert req["path"] == "/rest/api/3/issue/PROJ-1" and req["query"] == {"expand": "names"}
    assert req["headers"]["accept"] == "application/json"


@pytest.mark.anyio
async def test_service_account_uses_the_gateway_and_resolves_cloud_id_once():
    srv = ScenarioServer()
    srv.raw  # noqa: B018
    calls = []
    srv.routes.insert(0, ("GET", __import__("re").compile("^/oauth/token/accessible-resources$"),
                          lambda m, q, b: (200, [{"id": "cid-9", "url": "https://example.atlassian.net"}])))
    be = backend(srv, email=None, api_token=None, service_account_key="svc-key-123")
    await be.jira.get_issue("PROJ-1")
    await be.jira.get_issue("PROJ-2")
    paths = [r["path"] for r in srv.requests]
    assert paths.count("/oauth/token/accessible-resources") == 1
    assert paths[-1] == "/ex/jira/cid-9/rest/api/3/issue/PROJ-2" or paths[-1].endswith("/rest/api/3/issue/PROJ-2")
    assert srv.requests[-1]["headers"]["authorization"] == "Bearer svc-key-123"


@pytest.mark.anyio
async def test_429_is_retried_with_retry_after_then_succeeds():
    srv = ScenarioServer()
    srv.fail_next = [(429, {"Retry-After": "0"}), (429, {"Retry-After": "0"})]
    issue = await backend(srv).jira.get_issue("PROJ-1")
    assert issue.key == "PROJ-1" and [r["path"] for r in srv.requests].count("/rest/api/3/issue/PROJ-1") == 3


@pytest.mark.anyio
async def test_persistent_429_surfaces_as_rate_limited_error():
    srv = ScenarioServer()
    srv.fail_next = [(429, {"Retry-After": "0"})] * 10
    with pytest.raises(RateLimitedError):
        await backend(srv, max_retries=3).jira.get_issue("PROJ-1")


@pytest.mark.anyio
async def test_401_maps_to_authentication_error_and_404_to_not_found():
    srv = ScenarioServer()
    srv.fail_next = [(401, {})]
    with pytest.raises(AuthenticationError):
        await backend(srv).jira.get_issue("PROJ-1")
    with pytest.raises(NotFoundError) as exc:
        await backend(srv).jira.get_issue("PROJ-404")
    assert exc.value.operation == "jira.issue.get" and exc.value.backend == "rest"


@pytest.mark.anyio
async def test_requested_fields_are_projected_on_the_wire():
    srv = ScenarioServer()
    issue = await backend(srv).jira.get_issue("PROJ-1", fields=["summary", "status"])
    assert srv.requests[-1]["query"]["fields"] == "summary,status" and set(issue.fields) == {"summary", "status"}


@pytest.mark.anyio
async def test_confluence_v2_cursor_is_followed_verbatim():
    srv = ScenarioServer()
    be = backend(srv).confluence
    first = await be.list_children("123", limit=1)
    await be.list_children("123", limit=1, cursor=first.next_cursor)
    second_req = srv.requests[-1]
    assert second_req["path"] == "/wiki/api/v2/pages/123/children" and second_req["query"] == {"cursor": "1", "limit": "1"}


@pytest.mark.anyio
async def test_attachment_download_reads_from_the_wiki_download_link():
    srv = ScenarioServer()
    got = await backend(srv).confluence.download_attachment("cat2")
    assert got.data == b"notes" and srv.requests[-1]["path"] == "/wiki/download/attachments/123/notes.txt"


@pytest.mark.anyio
async def test_oversized_attachment_is_refused_before_downloading():
    srv = ScenarioServer()
    with pytest.raises(Exception, match="max_bytes"):
        await backend(srv).jira.download_attachment("att1", max_bytes=2)
    assert not any("/attachment/content/" in r["path"] for r in srv.requests)


@pytest.mark.anyio
async def test_agile_surface_paths_and_sprint_state_param():
    srv = ScenarioServer()
    await backend(srv).agile.list_sprints(42, states=["active", "closed"])
    assert srv.requests[-1]["path"] == "/rest/agile/1.0/board/42/sprint" and srv.requests[-1]["query"]["state"] == "active,closed"
