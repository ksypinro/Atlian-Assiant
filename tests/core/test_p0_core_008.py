import base64
import hashlib
import hmac
import json

import httpx
import pytest

from atlian_assiant.core.auth import (
    AuthStrategy,
    BasicTokenAuth,
    ConnectJwtAuth,
    DcBasicAuth,
    OrgApiKeyAuth,
    PersonalAccessTokenAuth,
    ServiceAccountKeyAuth,
)
from atlian_assiant.core.urls import ApiSurface


async def header(auth, url="https://example.atlassian.net/rest/api/3/myself", method="GET"):
    req = httpx.Request(method, url)
    await auth.apply(req)
    return req.headers["authorization"]


@pytest.mark.anyio
async def test_each_strategy_sets_its_documented_header():
    assert await header(BasicTokenAuth("e@x.com", "tok")) == "Basic " + base64.b64encode(b"e@x.com:tok").decode()
    assert await header(ServiceAccountKeyAuth("key")) == "Bearer key"
    assert await header(PersonalAccessTokenAuth("pat")) == "Bearer pat"
    assert await header(OrgApiKeyAuth("org")) == "Bearer org"
    assert await header(DcBasicAuth("u", "p")) == "Basic " + base64.b64encode(b"u:p").decode()


def test_gateway_flags_and_surface_support():
    assert not BasicTokenAuth("a", "b").uses_gateway and ServiceAccountKeyAuth("k").uses_gateway
    assert BasicTokenAuth("a", "b").supports(ApiSurface.CONFLUENCE_V2) and not BasicTokenAuth("a", "b").supports(ApiSurface.DC_JIRA)
    assert PersonalAccessTokenAuth("p").supports(ApiSurface.DC_JIRA) and not PersonalAccessTokenAuth("p").supports(ApiSurface.JIRA_PLATFORM)
    assert OrgApiKeyAuth("k").supports(ApiSurface.ORG_ADMIN) and not OrgApiKeyAuth("k").supports(ApiSurface.JIRA_PLATFORM)
    for a in (BasicTokenAuth("a", "b"), ServiceAccountKeyAuth("k"), PersonalAccessTokenAuth("p"), OrgApiKeyAuth("k")):
        assert isinstance(a, AuthStrategy)


def test_reprs_never_leak_secrets():
    for a in (BasicTokenAuth("me@x.com", "SECRET"), ServiceAccountKeyAuth("SECRET"), PersonalAccessTokenAuth("SECRET"),
              DcBasicAuth("me", "SECRET"), OrgApiKeyAuth("SECRET"), ConnectJwtAuth("key", "SECRET")):
        assert "SECRET" not in repr(a) and "me@x.com" not in repr(a)


@pytest.mark.anyio
async def test_connect_jwt_is_a_valid_hs256_token_with_query_string_hash():
    auth = ConnectJwtAuth("client-key", "shared-secret")
    h = await header(auth, "https://example.atlassian.net/rest/api/3/issue/X-1?b=2&a=1&jwt=zzz")
    assert h.startswith("JWT ")
    head, payload, sig = h[4:].split(".")
    expected = base64.urlsafe_b64encode(hmac.new(b"shared-secret", f"{head}.{payload}".encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    assert sig == expected
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    assert claims["iss"] == "client-key" and claims["exp"] - claims["iat"] == 180
    assert claims["qsh"] == hashlib.sha256(b"GET&/rest/api/3/issue/X-1&a=1&b=2").hexdigest()      # jwt param excluded, sorted
