import httpx
import pytest

from atlassian_client.core.auth import BasicTokenAuth, ServiceAccountKeyAuth
from atlassian_client.core.errors import ConfigurationError
from atlassian_client.core.urls import ApiSurface, CloudIdResolver, UrlResolver


def test_basic_auth_urls():
    u = UrlResolver("example.atlassian.net", None, gateway=False)
    assert u.base(ApiSurface.JIRA_PLATFORM) == "https://example.atlassian.net/rest/api/3"
    assert u.base(ApiSurface.JIRA_AGILE) == "https://example.atlassian.net/rest/agile/1.0"
    assert u.base(ApiSurface.JSM) == "https://example.atlassian.net/rest/servicedeskapi"
    assert u.base(ApiSurface.CONFLUENCE_V2) == "https://example.atlassian.net/wiki/api/v2"
    assert u.base(ApiSurface.CONFLUENCE_V1) == "https://example.atlassian.net/wiki/rest/api"
    assert u.base(ApiSurface.GRAPHQL) == "https://example.atlassian.net/gateway/api/graphql"


def test_gateway_urls_need_cloud_id():
    u = UrlResolver("example.atlassian.net", "cid-1", gateway=True)
    assert u.base(ApiSurface.JIRA_PLATFORM) == "https://api.atlassian.com/ex/jira/cid-1/rest/api/3"
    assert u.base(ApiSurface.CONFLUENCE_V2) == "https://api.atlassian.com/ex/confluence/cid-1/wiki/api/v2"
    assert u.base(ApiSurface.GRAPHQL) == "https://api.atlassian.com/graphql"
    with pytest.raises(ConfigurationError) as exc:
        UrlResolver("x", None, gateway=True).base(ApiSurface.JIRA_PLATFORM)
    assert "cloud_id" in str(exc.value)


def test_org_and_dc_surfaces():
    u = UrlResolver(None, None, False, org_id="o1", dc_jira_base="https://jira.corp/", dc_confluence_base="https://wiki.corp")
    assert u.base(ApiSurface.ORG_ADMIN) == "https://api.atlassian.com/admin/v1/orgs/o1"
    assert u.base(ApiSurface.DC_JIRA) == "https://jira.corp/rest/api/2"
    assert u.base(ApiSurface.DC_CONFLUENCE) == "https://wiki.corp/rest/api"
    with pytest.raises(ConfigurationError):
        UrlResolver("x", None, False).base(ApiSurface.ORG_ADMIN)


def test_site_prefix_is_stripped():
    assert UrlResolver("https://example.atlassian.net/", None, False).base(ApiSurface.JIRA_PLATFORM).startswith("https://example.atlassian.net/rest")


@pytest.mark.anyio
async def test_cloud_id_via_tenant_info_is_cached():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(200, json={"cloudId": "abc"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    r = CloudIdResolver("example.atlassian.net", BasicTokenAuth("a", "b"), http)
    assert await r.resolve() == "abc" and await r.resolve() == "abc"
    assert calls == ["https://example.atlassian.net/_edge/tenant_info"]


@pytest.mark.anyio
async def test_cloud_id_via_oauth_accessible_resources_matches_site():
    sites = [{"id": "one", "url": "https://other.atlassian.net"}, {"id": "two", "url": "https://example.atlassian.net"}]
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=sites)))
    assert await CloudIdResolver("example.atlassian.net", ServiceAccountKeyAuth("k"), http).resolve() == "two"
    with pytest.raises(ConfigurationError):
        await CloudIdResolver("nope.atlassian.net", ServiceAccountKeyAuth("k"), http).resolve()
    assert await CloudIdResolver("x", ServiceAccountKeyAuth("k"), http, configured="cfg").resolve() == "cfg"
