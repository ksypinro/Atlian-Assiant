"""Webhook receivers, verifiers, response semantics, URL preflight and framework adapters (design 14.5)."""
from __future__ import annotations

import hmac
import json
import time

import httpx
import jwt
import pytest

from atlian_assiant.core.errors import ConfigurationError, WebhookVerificationError
from atlian_assiant.events.receivers import (
    AllOf,
    AutomationWebRequestReceiver,
    ConnectJwtVerifier,
    ForgeInvocationTokenVerifier,
    HmacSignatureVerifier,
    InboundRequest,
    IpAllowlistVerifier,
    JiraWebhookReceiver,
    JwtVerifier,
    ReplayWindowVerifier,
    StaticHeaderVerifier,
    WebhookEndpoint,
    WebhookUrlPreflight,
    as_asgi_app,
    connect_qsh,
    fastapi_router,
    flask_blueprint,
    parse_ip_ranges,
)
from atlian_assiant.events.receivers.preflight import ProbeResult, is_public
from atlian_assiant.events.stores import MemoryLocalQueue

pytestmark = [pytest.mark.layer_11, pytest.mark.anyio]

BODY = json.dumps({"webhookEvent": "jira:issue_updated", "timestamp": int(time.time() * 1000),
                   "issue": {"id": "1", "key": "PROJ-1", "fields": {"updated": "2026-09-01T12:00:00.000+0000", "project": {"key": "PROJ"}}}}).encode()


def req(body: bytes = BODY, *, headers=None, method="POST", path="/hooks/jira", query=None, remote="3.3.3.3") -> InboundRequest:
    return InboundRequest(method, path, {"X-Atlassian-Webhook-Identifier": "wh-1", **(headers or {})}, body, query, remote)


def sign(body: bytes, secret: str = "s3cret", algo: str = "sha256") -> str:
    return f"{algo}=" + hmac.new(secret.encode(), body, algo).hexdigest()


# ---------------------------------------------------------------- response semantics (design table)
async def test_verified_and_enqueued_returns_202_and_a_duplicate_returns_200():
    q = MemoryLocalQueue()
    ep = JiraWebhookReceiver(q, verifiers=[HmacSignatureVerifier("s3cret")])
    first = await ep.handle(req(headers={"X-Hub-Signature": sign(BODY)}))
    again = await ep.handle(req(headers={"X-Hub-Signature": sign(BODY)}))
    assert (first.status, again.status) == (202, 200) and await q.depth() == 1 and ep.metrics == {"accepted": 1, "duplicate": 1}


async def test_failed_verification_returns_401_and_enqueues_nothing():
    q = MemoryLocalQueue()
    ep = JiraWebhookReceiver(q, verifiers=[HmacSignatureVerifier("s3cret")])
    assert (await ep.handle(req(headers={"X-Hub-Signature": sign(BODY, "wrong")}))).status == 401
    assert (await ep.handle(req())).status == 401
    assert await q.depth() == 0 and ep.metrics["unauthorized"] == 2


async def test_unavailable_queue_returns_503_so_atlassian_retries():
    class Broken(MemoryLocalQueue):
        async def put(self, event):
            raise OSError("disk full")
    ep = JiraWebhookReceiver(Broken())
    assert (await ep.handle(req())).status == 503 and ep.metrics == {"queue_unavailable": 1}
    assert not await ep.receipts.seen("jira_webhook:wh-1")                      # a failed enqueue must not poison the retry as a "duplicate"
    assert (await JiraWebhookReceiver(MemoryLocalQueue()).handle(req())).status == 202


async def test_overload_returns_429():
    q = MemoryLocalQueue()
    ep = JiraWebhookReceiver(q, max_queue_depth=1)
    assert (await ep.handle(req())).status == 202
    assert (await ep.handle(req(headers={"X-Atlassian-Webhook-Identifier": "wh-2"}))).status == 429 and ep.metrics["overloaded"] == 1


async def test_malformed_wrong_method_wrong_path_and_oversized_requests_never_enqueue():
    q = MemoryLocalQueue()
    ep = JiraWebhookReceiver(q, max_body_bytes=2000)
    assert (await ep.handle(req(b"not json"))).status == 400
    assert (await ep.handle(req(b"[1,2]"))).status == 400
    assert (await ep.handle(req(method="GET"))).status == 405
    assert (await ep.handle(req(path="/elsewhere"))).status == 404
    assert (await ep.handle(req(b"x" * 3000))).status == 413
    assert await q.depth() == 0


async def test_responses_are_never_redirects():
    ep = JiraWebhookReceiver(MemoryLocalQueue())
    for r in (req(), req(b"nope"), req(method="PUT"), req(path="/x")):
        assert not 300 <= (await ep.handle(r)).status < 400


async def test_retries_with_the_same_identifier_are_deduplicated_across_attempts():
    ep = JiraWebhookReceiver(MemoryLocalQueue())
    statuses = [(await ep.handle(req(headers={"X-Atlassian-Webhook-Retry": str(n)}))).status for n in range(4)]
    assert statuses == [202, 200, 200, 200]


async def test_flows_are_tracked_as_separate_latency_series():
    ep = JiraWebhookReceiver(MemoryLocalQueue())
    fresh = json.dumps({**json.loads(BODY), "timestamp": int(time.time() * 1000)}).encode()     # BODY's timestamp is from import time
    await ep.handle(req(fresh, headers={"X-Atlassian-Webhook-Flow": "Primary", "X-Atlassian-Webhook-Identifier": "a"}))
    await ep.handle(req(fresh, headers={"X-Atlassian-Webhook-Flow": "Secondary", "X-Atlassian-Webhook-Identifier": "b"}))
    assert set(ep.latency_by_flow) == {"Primary", "Secondary"} and all(v[0] < 60 for v in ep.latency_by_flow.values())


async def test_enqueued_events_are_normalized_and_marked_verified():
    q = MemoryLocalQueue()
    await JiraWebhookReceiver(q, verifiers=[StaticHeaderVerifier("X-Token", "t")]).handle(req(headers={"X-Token": "t"}))
    item = await q.get()
    assert item is not None and item[1].entity_key == "PROJ-1" and item[1].delivery.verified and item[1].delivery.method_id == "DM-03"


async def test_automation_receiver_dedups_on_the_envelope_id_and_labels_confluence_as_dm_16():
    q = MemoryLocalQueue()
    ep = AutomationWebRequestReceiver(q, verifiers=[StaticHeaderVerifier("X-Rule-Token", "tok")])
    env = json.dumps({"atl_event_id": "9:v1:rule", "product": "confluence", "trigger": "page_updated", "entity_type": "page", "entity_id": "9"}).encode()
    r = lambda: InboundRequest("POST", "/hooks/automation", {"X-Rule-Token": "tok"}, env)  # noqa: E731
    assert [(await ep.handle(r())).status for _ in range(2)] == [202, 200]
    item = await q.get()
    assert item is not None and item[1].event_id == "9:v1:rule" and item[1].delivery.method_id == "DM-16"


# ---------------------------------------------------------------- verifiers
async def test_hmac_verifier_accepts_prefixed_and_bare_signatures_and_rejects_tampering():
    v = HmacSignatureVerifier("k", header="X-Sig")
    digest = hmac.new(b"k", BODY, "sha256").hexdigest()
    await v.verify(req(headers={"X-Sig": f"sha256={digest}"}))
    await v.verify(req(headers={"x-sig": digest.upper()}))
    with pytest.raises(WebhookVerificationError):
        await v.verify(req(BODY + b" ", headers={"X-Sig": f"sha256={digest}"}))
    with pytest.raises(WebhookVerificationError):
        await HmacSignatureVerifier("k", algorithm="sha1").verify(req(headers={"X-Hub-Signature": f"sha256={digest}"}))
    with pytest.raises(ConfigurationError):
        HmacSignatureVerifier("k", algorithm="nope")


async def test_static_header_verifier_is_exact():
    v = StaticHeaderVerifier("Authorization", "Bearer abc")
    await v.verify(req(headers={"authorization": "Bearer abc"}))
    for bad in ({}, {"Authorization": "Bearer abd"}, {"Authorization": "bearer abc"}):
        with pytest.raises(WebhookVerificationError):
            await v.verify(req(headers=bad))


async def test_jwt_verifier_checks_signature_expiry_and_issuer():
    good = jwt.encode({"iss": "app-1", "exp": time.time() + 60}, "k" * 32, algorithm="HS256")
    expired = jwt.encode({"iss": "app-1", "exp": time.time() - 60}, "k" * 32, algorithm="HS256")
    other = jwt.encode({"iss": "evil", "exp": time.time() + 60}, "k" * 32, algorithm="HS256")
    forged = jwt.encode({"iss": "app-1", "exp": time.time() + 60}, "z" * 32, algorithm="HS256")
    v = JwtVerifier("app-1", lambda issuer: "k" * 32)
    await v.verify(req(headers={"Authorization": f"JWT {good}"}))
    for token in (expired, other, forged):
        with pytest.raises(WebhookVerificationError):
            await v.verify(req(headers={"Authorization": f"JWT {token}"}))
    with pytest.raises(WebhookVerificationError):
        await v.verify(req())


class Tenants:
    async def shared_secret(self, client_key):
        return "t" * 32 if client_key == "tenant-1" else None


async def test_connect_jwt_verifier_uses_the_tenant_secret_and_validates_qsh():
    path, query = "/hooks/connect", {"a": "1"}
    qsh = connect_qsh("POST", path, query)
    token = jwt.encode({"iss": "tenant-1", "exp": time.time() + 60, "qsh": qsh}, "t" * 32, algorithm="HS256")
    v = ConnectJwtVerifier(Tenants())
    await v.verify(req(path=path, query=query, headers={"Authorization": f"JWT {token}"}))
    with pytest.raises(WebhookVerificationError):                                    # a token minted for another request
        await v.verify(req(path=path, query={"a": "2"}, headers={"Authorization": f"JWT {token}"}))
    stranger = jwt.encode({"iss": "tenant-9", "exp": time.time() + 60}, "t" * 32, algorithm="HS256")
    with pytest.raises(WebhookVerificationError):
        await v.verify(req(path=path, query=query, headers={"Authorization": f"JWT {stranger}"}))
    in_query = jwt.encode({"iss": "tenant-1", "exp": time.time() + 60, "qsh": connect_qsh("POST", path, {})}, "t" * 32, algorithm="HS256")
    await v.verify(req(path=path, query={"jwt": in_query}))


async def test_forge_invocation_token_verifier_requires_the_apps_audience():
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode({"aud": "app-1", "exp": time.time() + 60}, key, algorithm="RS256")
    wrong = jwt.encode({"aud": "app-2", "exp": time.time() + 60}, key, algorithm="RS256")
    v = ForgeInvocationTokenVerifier("app-1", "https://forge.example/jwks", lambda _: key.public_key())  # type: ignore[arg-type, return-value]
    await v.verify(req(headers={"Authorization": f"Bearer {token}"}))
    with pytest.raises(WebhookVerificationError):
        await v.verify(req(headers={"Authorization": f"Bearer {wrong}"}))


async def test_ip_allowlist_verifier_and_tolerant_feed_parsing():
    v = IpAllowlistVerifier(["18.184.99.128/25", "2600:1f14::/36"])
    await v.verify(req(remote="18.184.99.200"))
    for bad in ("8.8.8.8", None, "not-an-ip"):
        with pytest.raises(WebhookVerificationError):
            await v.verify(req(remote=bad))
    feeds = [json.dumps({"items": [{"cidr": "13.52.5.96/28"}, {"cidr": "bad"}]}), "13.52.5.96/28\n3.3.3.3", ["13.52.5.96/28"], {"ranges": [{"ipv4_prefix": "13.52.5.96/28"}]}]
    for feed in feeds:
        assert any(str(n) == "13.52.5.96/28" for n in parse_ip_ranges(feed)), feed

    async def loader():
        return {"items": [{"cidr": "9.9.9.0/24"}]}
    assert await v.refresh(loader) == 1
    await v.verify(req(remote="9.9.9.9"))

    async def broken():
        return "garbage"
    assert await v.refresh(broken) == 1                                              # an unparsable feed never empties a working list
    await v.verify(req(remote="9.9.9.9"))


async def test_replay_window_rejects_stale_payload_timestamps_but_allows_missing_ones():
    now = 1_800_000_000.0
    v = ReplayWindowVerifier(max_age=60, clock=lambda: now)
    await v.verify(req(json.dumps({"timestamp": int(now * 1000) - 30_000}).encode()))
    await v.verify(req(b'{"no": "timestamp"}'))
    await v.verify(req(b"not json"))
    with pytest.raises(WebhookVerificationError):
        await v.verify(req(json.dumps({"timestamp": int(now * 1000) - 3_600_000}).encode()))
    with pytest.raises(WebhookVerificationError):
        await ReplayWindowVerifier(60, header="X-Date", clock=lambda: now).verify(req(headers={"X-Date": "2020-01-01T00:00:00Z"}))


async def test_all_of_requires_every_verifier():
    ok, no = StaticHeaderVerifier("A", "1"), StaticHeaderVerifier("B", "2")
    await AllOf(ok, no).verify(req(headers={"A": "1", "B": "2"}))
    with pytest.raises(WebhookVerificationError):
        await AllOf(ok, no).verify(req(headers={"A": "1"}))


# ---------------------------------------------------------------- URL preflight
def fixed_resolver(*addresses: str):
    async def resolve(host: str, port: int) -> list[str]:
        return list(addresses)
    return resolve


def prober(result: ProbeResult):
    async def probe(url: str) -> ProbeResult:
        return result
    return probe


@pytest.mark.parametrize("address", ["10.1.2.3", "172.16.0.9", "192.168.1.5", "127.0.0.1", "169.254.169.254", "100.64.0.1", "fd00::1", "::1", "0.0.0.0", "224.0.0.1", "::ffff:10.0.0.1"])
async def test_preflight_rejects_every_non_public_address(address):
    pre = WebhookUrlPreflight(resolver=fixed_resolver(address), prober=prober(ProbeResult(True, status=200)))
    report = await pre.check("https://hooks.example.com/jira")
    assert not report.ok and any("not publicly routable" in p for p in report.problems) and not is_public(address)


async def test_preflight_rejects_ip_literals_http_bad_ports_credentials_and_unresolvable_hosts():
    pre = WebhookUrlPreflight(resolver=fixed_resolver("8.8.8.8"), prober=prober(ProbeResult(True, status=200)))
    assert not (await pre.check("https://10.0.0.5/jira")).ok
    assert any("https" in p for p in (await pre.check("http://hooks.example.com/jira")).problems)
    assert any("port 8443" in p for p in (await pre.check("https://hooks.example.com:8443/jira")).problems)
    assert any("credentials" in p for p in (await pre.check("https://u:p@hooks.example.com/jira")).problems)
    assert not (await pre.check("https:///nohost")).ok and not (await pre.check("https://hooks.example.com:notaport/x")).ok

    async def nxdomain(host, port):
        raise OSError("name or service not known")
    assert any("does not resolve" in p for p in (await WebhookUrlPreflight(resolver=nxdomain).check("https://nope.invalid/x")).problems)


async def test_preflight_ports_are_configurable_because_the_documented_set_is_unverified():
    pre = WebhookUrlPreflight(allowed_ports=frozenset({443, 8443}), resolver=fixed_resolver("8.8.8.8"), prober=prober(ProbeResult(True, status=202)))
    assert (await pre.check("https://hooks.example.com:8443/jira")).ok


async def test_preflight_rejects_bad_tls_and_redirects_and_accepts_a_clean_receiver():
    resolver = fixed_resolver("8.8.8.8")
    bad_tls = await WebhookUrlPreflight(resolver=resolver, prober=prober(ProbeResult(False, "CERTIFICATE_VERIFY_FAILED"))).check("https://hooks.example.com/j")
    assert not bad_tls.ok and "TLS validation failed" in bad_tls.problems[0]
    redirect = await WebhookUrlPreflight(resolver=resolver, prober=prober(ProbeResult(True, status=301, location="https://elsewhere"))).check("https://hooks.example.com/j")
    assert not redirect.ok and "redirect" in redirect.problems[0]
    assert (await WebhookUrlPreflight(resolver=resolver, prober=prober(ProbeResult(True, status=401))).check("https://hooks.example.com/j")).ok


async def test_default_probe_never_follows_redirects_and_reports_status(monkeypatch):
    seen = {}

    class FakeClient:
        def __init__(self, **kw):
            seen.update(kw)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, **kw):
            return httpx.Response(302, headers={"location": "https://other.example.com"})
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    report = await WebhookUrlPreflight(resolver=fixed_resolver("8.8.8.8")).check("https://hooks.example.com/j")
    assert seen["follow_redirects"] is False and seen["verify"] is True and not report.ok and "302" in report.problems[0]


# ---------------------------------------------------------------- adapters
async def test_asgi_adapter_routes_by_path_and_returns_the_endpoint_status():
    q = MemoryLocalQueue()
    ep: WebhookEndpoint = JiraWebhookReceiver(q, verifiers=[StaticHeaderVerifier("X-T", "t")])
    app = as_asgi_app([ep], max_body_bytes=10_000)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://hooks") as http:
        ok = await http.post("/hooks/jira", content=BODY, headers={"X-T": "t", "X-Atlassian-Webhook-Identifier": "w1"})
        dup = await http.post("/hooks/jira", content=BODY, headers={"X-T": "t", "X-Atlassian-Webhook-Identifier": "w1"})
        forged = await http.post("/hooks/jira", content=BODY, headers={"X-T": "x"})
        missing = await http.post("/nope", content=b"{}")
        huge = await http.post("/hooks/jira", content=b"x" * 20_000, headers={"X-T": "t"})
    assert [r.status_code for r in (ok, dup, forged, missing, huge)] == [202, 200, 401, 404, 413] and await q.depth() == 1


async def test_asgi_adapter_passes_query_parameters_for_connect_style_endpoints():
    q = MemoryLocalQueue()
    ep = JiraWebhookReceiver(q, verifiers=[ConnectJwtVerifier(Tenants())])
    token = jwt.encode({"iss": "tenant-1", "exp": time.time() + 60, "qsh": connect_qsh("POST", "/hooks/jira", {})}, "t" * 32, algorithm="HS256")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=as_asgi_app([ep])), base_url="http://hooks") as http:
        assert (await http.post(f"/hooks/jira?jwt={token}", content=BODY, headers={"X-Atlassian-Webhook-Identifier": "q1"})).status_code == 202


def test_fastapi_and_flask_adapters_build_routes_when_the_frameworks_are_installed():
    ep = JiraWebhookReceiver(MemoryLocalQueue())
    try:
        import fastapi  # noqa: F401
        router = fastapi_router([ep])
        assert any(r.path == "/hooks/jira" for r in router.routes)
    except ImportError:
        with pytest.raises(ImportError, match="fastapi"):
            fastapi_router([ep])
    try:
        import flask  # noqa: F401
        assert flask_blueprint([ep]).name == "atlassian_events"
    except ImportError:
        with pytest.raises(ImportError, match="flask"):
            flask_blueprint([ep])

