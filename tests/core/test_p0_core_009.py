import json
import stat

import httpx
import pytest

from atlian_assiant.core.auth import OAuth3LOAuth
from atlian_assiant.core.errors import AuthenticationError


def make(handler, tmp_path, refresh="r1"):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OAuth3LOAuth("cid", "csecret", refresh, store=tmp_path / "tok.json", http=http)


@pytest.mark.anyio
async def test_refresh_gets_access_token_and_rotated_refresh_token_is_persisted_0600(tmp_path):
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={"access_token": "A1", "expires_in": 3600, "refresh_token": "r2"})

    auth = make(handler, tmp_path)
    req = httpx.Request("GET", "https://api.atlassian.com/x")
    await auth.apply(req)
    assert req.headers["authorization"] == "Bearer A1"
    assert bodies[0]["grant_type"] == "refresh_token" and bodies[0]["refresh_token"] == "r1"
    store = tmp_path / "tok.json"
    assert json.loads(store.read_text()) == {"refresh_token": "r2"}
    assert stat.S_IMODE(store.stat().st_mode) == 0o600


@pytest.mark.anyio
async def test_persisted_token_wins_on_next_start_and_access_token_is_cached(tmp_path):
    calls = []

    def handler(req):
        calls.append(json.loads(req.content)["refresh_token"])
        return httpx.Response(200, json={"access_token": "A", "expires_in": 3600})

    (tmp_path / "tok.json").write_text('{"refresh_token": "persisted"}')
    auth = make(handler, tmp_path)
    for _ in range(3):
        await auth.apply(httpx.Request("GET", "https://api.atlassian.com/x"))
    assert calls == ["persisted"]


@pytest.mark.anyio
async def test_expired_token_is_refreshed(tmp_path):
    n = {"c": 0}

    def handler(req):
        n["c"] += 1
        return httpx.Response(200, json={"access_token": f"A{n['c']}", "expires_in": 1})

    auth = make(handler, tmp_path)
    await auth.apply(httpx.Request("GET", "https://api.atlassian.com/x"))
    await auth.apply(httpx.Request("GET", "https://api.atlassian.com/x"))      # expires_in=1 < 30s margin
    assert n["c"] == 2


@pytest.mark.anyio
async def test_failed_refresh_raises_authentication_error_with_hint(tmp_path):
    auth = make(lambda r: httpx.Response(400, json={"error": "invalid_grant"}), tmp_path)
    with pytest.raises(AuthenticationError) as exc:
        await auth.refresh()
    assert "re-run" in exc.value.hint and "r1" not in repr(auth)
