import httpx
import pytest

from atlassian_client.core.auth import BasicTokenAuth, ServiceAccountKeyAuth
from atlassian_client.core.errors import (AuthenticationError, ConfigurationError, NotFoundError, RateLimitedError, ServerError)
from atlassian_client.core.http import HttpTransport, RequestSpec
from atlassian_client.core.retry import RetryPolicy
from atlassian_client.core.urls import ApiSurface, UrlResolver

J = ApiSurface.JIRA_PLATFORM


def transport(handler, auth=None, *, gateway=False, cloud_id=None, pinned=None, sleeps=None, retry=None):
    async def fake_sleep(s: float) -> None:
        if sleeps is not None:
            sleeps.append(s)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    auth = auth or BasicTokenAuth("a@b.c", "tok")
    return HttpTransport(http, auth, UrlResolver("example.atlassian.net", cloud_id, gateway), retry or RetryPolicy(max_attempts=4),
                         pinned_cloud_id=pinned, sleep=fake_sleep)


@pytest.mark.anyio
async def test_injects_auth_accept_and_builds_url():
    seen = {}

    def handler(req):
        seen.update(url=str(req.url), auth=req.headers["authorization"], accept=req.headers["accept"])
        return httpx.Response(200, json={"ok": 1})

    t = transport(handler)
    assert await t.json(RequestSpec("GET", J, "/myself", {"expand": "x", "skip": None})) == {"ok": 1}
    assert seen["url"] == "https://example.atlassian.net/rest/api/3/myself?expand=x"
    assert seen["auth"].startswith("Basic ") and seen["accept"] == "application/json"


@pytest.mark.anyio
async def test_retries_429_honouring_retry_after_then_succeeds():
    n = {"c": 0}
    sleeps: list[float] = []

    def handler(req):
        n["c"] += 1
        return httpx.Response(429, headers={"Retry-After": "7"}) if n["c"] < 3 else httpx.Response(200, json={})

    await transport(handler, sleeps=sleeps).send(RequestSpec("GET", J, "/x"))
    assert n["c"] == 3 and len(sleeps) == 2 and all(7 <= s <= 8 for s in sleeps)


@pytest.mark.anyio
async def test_gives_up_after_max_attempts_with_rate_limited_error():
    n = {"c": 0}

    def handler(req):
        n["c"] += 1
        return httpx.Response(429, headers={"Retry-After": "1"})

    with pytest.raises(RateLimitedError) as exc:
        await transport(handler).send(RequestSpec("GET", J, "/x"))
    assert n["c"] == 4 and exc.value.retry_after == 1 and exc.value.retryable


@pytest.mark.anyio
async def test_non_idempotent_requests_are_only_retried_on_429():
    n = {"c": 0}

    def handler(req):
        n["c"] += 1
        return httpx.Response(503)

    with pytest.raises(ServerError):
        await transport(handler).send(RequestSpec("POST", J, "/issue", idempotent=False))
    assert n["c"] == 1                                   # a 503 may have been processed: never replay a write
    n["c"] = 0
    with pytest.raises(ServerError):
        await transport(handler).send(RequestSpec("GET", J, "/issue"))
    assert n["c"] == 4


@pytest.mark.anyio
async def test_error_mapping_extracts_jira_messages():
    def handler(req):
        return httpx.Response(404, json={"errorMessages": ["Issue Does Not Exist"], "errors": {}})

    with pytest.raises(NotFoundError) as exc:
        await transport(handler).send(RequestSpec("GET", J, "/issue/X-1", operation="jira.issue.get"))
    assert "Issue Does Not Exist" in str(exc.value) and exc.value.operation == "jira.issue.get" and exc.value.backend == "rest"
    assert exc.value.details["errorMessages"] == ["Issue Does Not Exist"]


@pytest.mark.anyio
async def test_401_triggers_one_refresh_then_retries_once():
    class Refreshing(BasicTokenAuth):
        refreshes = 0

        async def refresh(self) -> bool:
            Refreshing.refreshes += 1
            return True

    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(401)

    with pytest.raises(AuthenticationError):
        await transport(handler, Refreshing("a", "b")).send(RequestSpec("GET", J, "/x"))
    assert calls["n"] == 2 and Refreshing.refreshes == 1


@pytest.mark.anyio
async def test_unsupported_surface_for_auth_is_a_configuration_error():
    t = transport(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ConfigurationError):
        await t.send(RequestSpec("GET", ApiSurface.ORG_ADMIN, "/events"))


@pytest.mark.anyio
async def test_cloud_id_pinning_blocks_other_tenants():
    t = transport(lambda r: httpx.Response(200, json={}), ServiceAccountKeyAuth("k"), gateway=True, cloud_id="other", pinned="mine")
    with pytest.raises(ConfigurationError):
        await t.send(RequestSpec("GET", J, "/myself"))
    ok = transport(lambda r: httpx.Response(200, json={}), ServiceAccountKeyAuth("k"), gateway=True, cloud_id="mine", pinned="mine")
    assert (await ok.send(RequestSpec("GET", J, "/myself"))).status_code == 200


@pytest.mark.anyio
async def test_absolute_urls_only_on_atlassian_hosts():
    t = transport(lambda r: httpx.Response(200, json={}))
    assert (await t.send(RequestSpec("GET", J, "https://example.atlassian.net/wiki/download/x"))).status_code == 200
    with pytest.raises(ConfigurationError):
        await t.send(RequestSpec("GET", J, "https://evil.example.com/steal"))


@pytest.mark.anyio
async def test_download_cap_and_empty_body():
    t = transport(lambda r: httpx.Response(200, content=b"x" * 10))
    assert len(await t.stream_download(RequestSpec("GET", J, "/a"), max_bytes=10)) == 10
    with pytest.raises(Exception) as exc:
        await t.stream_download(RequestSpec("GET", J, "/a"), max_bytes=5)
    assert "max_bytes" in str(exc.value)
    assert await transport(lambda r: httpx.Response(204)).json(RequestSpec("DELETE", J, "/a")) is None


@pytest.mark.anyio
async def test_telemetry_hooks_receive_redacted_headers():
    got = []
    t = transport(lambda r: httpx.Response(200, json={}))
    t.telemetry.on_request.append(got.append)
    await t.send(RequestSpec("GET", J, "/myself"))
    assert got and got[0]["headers"]["authorization"] == "***"
