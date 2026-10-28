"""DM-04: Connect webhook module, LEGACY (ConnectAppProvisioner, ConnectLifecycleEndpoint, ConnectWebhookReceiver, verify_dm_04)."""
from __future__ import annotations

import json
import time
from datetime import timedelta

import jwt
import pytest

from atlian_assiant.core.auth.static import ConnectJwtAuth
from atlian_assiant.core.errors import InvalidRequestError
from atlian_assiant.events.catalog import Ingress, method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.model import ChangeAction, EntityType, EventSourceKind
from atlian_assiant.events.provision.base import EventProvisioner
from atlian_assiant.events.provision.connect import CONNECT_DYNAMIC_LIMIT, ConnectAppProvisioner, descriptor
from atlian_assiant.events.receivers import InboundRequest, connect_qsh
from atlian_assiant.events.receivers.connect import (
    ConnectLifecycleEndpoint,
    ConnectWebhookReceiver,
    EncryptedFileConnectStore,
    MemoryConnectStore,
)
from atlian_assiant.events.stores import MemoryLocalQueue
from atlian_assiant.events.verify import verify_dm_04
from atlian_assiant.policy import CallbackConfirmation

from .sitesim import SiteSim, jira_body, run_stack

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)
SECRET = "s" * 32
BASE = "https://connect.example.com"


def token(secret=SECRET, iss="tenant-1", method_="POST", path="/hooks/connect", query=None, exp=60):
    return jwt.encode({"iss": iss, "exp": time.time() + exp, "qsh": connect_qsh(method_, path, query or {})}, secret, algorithm="HS256")


def test_dm_04_catalog_entry_is_flagged_legacy():
    m = method("DM-04")
    assert m.legacy and m.ingress is Ingress.REQUIRED and m.consumer.endswith("ConnectWebhookReceiver")
    assert isinstance(ConnectAppProvisioner(key="k", name="n", base_url=BASE, events=[], directory=None), EventProvisioner)        # type: ignore[arg-type]


def test_dm_04_descriptor_has_jwt_auth_lifecycle_routes_and_one_webhook_per_event():
    d = descriptor(key="my-app", name="My app", base_url=BASE + "/", events=["jira:issue_updated", "comment_created"])
    assert d["authentication"] == {"type": "jwt"} and d["lifecycle"] == {"installed": "/connect/installed", "uninstalled": "/connect/uninstalled"} and d["baseUrl"] == BASE
    assert d["modules"]["webhooks"] == [{"event": "jira:issue_updated", "url": "/hooks/connect"}, {"event": "comment_created", "url": "/hooks/connect"}]
    with pytest.raises(InvalidRequestError, match="unknown webhook events"):
        descriptor(key="k", name="n", base_url=BASE, events=["jira:nonsense"])
    with pytest.raises(InvalidRequestError, match="https"):
        descriptor(key="k", name="n", base_url="http://x", events=[])


async def test_dm_04_provision_writes_the_descriptor_warns_it_is_legacy_and_declined_writes_nothing(tmp_path):
    prov = ConnectAppProvisioner(key="my-app", name="My app", base_url=BASE, events=["jira:issue_updated"], directory=tmp_path)
    plan = await prov.plan()
    assert plan.method_id == "DM-04" and "legacy" in plan.warnings[0].lower() and any("ENCRYPTED" in s.description for s in plan.steps)
    from atlian_assiant.policy import DenyAll
    with pytest.raises(Exception, match="declined"):
        await prov.apply(plan, confirm=DenyAll())
    assert not (tmp_path / "atlassian-connect.json").exists()
    out = await prov.apply(plan, confirm=APPROVE)
    assert json.loads((tmp_path / "atlassian-connect.json").read_text())["key"] == "my-app" and out["webhook_url"] == BASE + "/hooks/connect"
    await prov.teardown(confirm=APPROVE)
    assert not (tmp_path / "atlassian-connect.json").exists()


async def test_dm_04_provision_dynamic_webhooks_respect_the_connect_limit_of_100(tmp_path):
    registered = []

    async def register(url, events):
        registered.append((url, list(events)))
        return ["101"]
    counts = {"n": 5}

    async def count():
        return counts["n"]
    prov = ConnectAppProvisioner(key="k", name="n", base_url=BASE, events=[], directory=tmp_path, register_dynamic=register, list_dynamic=count)
    assert await prov.ensure_dynamic(["jira:issue_updated"]) == ["101"] and registered[0][0] == BASE + "/hooks/connect"
    counts["n"] = CONNECT_DYNAMIC_LIMIT
    with pytest.raises(InvalidRequestError, match="maximum 100"):
        await prov.ensure_dynamic(["jira:issue_updated"])
    with pytest.raises(InvalidRequestError, match="registrar"):
        await ConnectAppProvisioner(key="k", name="n", base_url=BASE, events=[], directory=tmp_path).ensure_dynamic([])


async def test_dm_04_lifecycle_installed_stores_the_secret_and_a_forged_reinstall_cannot_replace_it():
    store = MemoryConnectStore()
    ep = ConnectLifecycleEndpoint(store)
    body = json.dumps({"clientKey": "tenant-1", "sharedSecret": SECRET, "baseUrl": "https://x.atlassian.net"}).encode()
    assert (await ep.handle(InboundRequest("POST", "/connect/installed", {}, body))).status == 204 and await store.shared_secret("tenant-1") == SECRET
    evil = json.dumps({"clientKey": "tenant-1", "sharedSecret": "attacker-secret"}).encode()
    assert (await ep.handle(InboundRequest("POST", "/connect/installed", {}, evil))).status == 401 and await store.shared_secret("tenant-1") == SECRET
    ok = InboundRequest("POST", "/connect/installed", {"Authorization": f"JWT {token(path='/connect/installed')}"}, json.dumps({"clientKey": "tenant-1", "sharedSecret": "rotated" * 5}).encode())
    assert (await ep.handle(ok)).status == 204 and await store.shared_secret("tenant-1") == "rotated" * 5
    assert (await ep.handle(InboundRequest("POST", "/connect/installed", {}, b"{}"))).status == 400 and (await ep.handle(InboundRequest("GET", "/connect/installed", {}, b""))).status == 405


async def test_dm_04_uninstall_wipes_the_secret_only_when_authenticated():
    store = MemoryConnectStore()
    await store.put("tenant-1", SECRET)
    ep = ConnectLifecycleEndpoint(store)
    body = json.dumps({"clientKey": "tenant-1"}).encode()
    assert (await ep.handle(InboundRequest("POST", "/connect/uninstalled", {}, body))).status == 401 and await store.shared_secret("tenant-1") == SECRET
    signed = InboundRequest("POST", "/connect/uninstalled", {"Authorization": f"JWT {token(path='/connect/uninstalled')}"}, body)
    assert (await ep.handle(signed)).status == 204 and await store.shared_secret("tenant-1") is None and await store.tenants() == []
    assert (await ep.handle(signed)).status == 204                                          # idempotent
    assert (await ep.handle(InboundRequest("POST", "/connect/other", {}, body))).status in (204, 404)


async def test_dm_04_consumer_verifies_the_tenant_jwt_qsh_and_expiry_and_dedups():
    store = MemoryConnectStore()
    await store.put("tenant-1", SECRET)
    q = MemoryLocalQueue()
    ep = ConnectWebhookReceiver(q, store)
    raw = json.dumps(jira_body("PROJ-1")).encode()

    def req(tok, ident="w1", query=None):
        return InboundRequest("POST", "/hooks/connect", {"Authorization": f"JWT {tok}", "X-Atlassian-Webhook-Identifier": ident}, raw, query)
    assert (await ep.handle(req(token()))).status == 202 and (await ep.handle(req(token()))).status == 200
    bad = [token(secret="x" * 32), token(iss="tenant-9"), token(exp=-10), token(path="/hooks/other"), token(query={"a": "1"})]
    assert [(await ep.handle(req(t, ident=f"b{i}"))).status for i, t in enumerate(bad)] == [401] * 5
    assert (await ep.handle(InboundRequest("POST", "/hooks/connect", {}, raw))).status == 401
    ev = (await q.get())[1]
    assert (ev.event_id, ev.source, ev.entity_type, ev.action, ev.entity_key, ev.delivery.method_id, ev.delivery.verified) == (
        "w1", EventSourceKind.CONNECT_WEBHOOK, EntityType.ISSUE, ChangeAction.UPDATED, "PROJ-1", "DM-04", True)


def test_dm_04_the_verifier_and_the_clients_signer_agree_on_the_query_string_hash():
    url = "https://connect.example.com/hooks/connect/?b=2&a=x%20y&jwt=zzz"
    signer = ConnectJwtAuth("tenant-1", SECRET)
    claims = jwt.decode(signer.token("POST", url), SECRET, algorithms=["HS256"])
    assert claims["qsh"] == connect_qsh("POST", "/hooks/connect/", {"b": "2", "a": "x y"}) == ConnectJwtAuth.query_string_hash("POST", url)


def test_dm_04_encrypted_store_keeps_secrets_encrypted_at_rest_and_owner_only(tmp_path):
    key = EncryptedFileConnectStore.generate_key()
    path = tmp_path / "connect.enc"
    store = EncryptedFileConnectStore(path, key)
    import anyio

    async def go():
        await store.put("tenant-1", SECRET, "https://x.atlassian.net")
        await store.put("tenant-2", "other-secret")
        assert await store.shared_secret("tenant-1") == SECRET and await store.tenants() == ["tenant-1", "tenant-2"]
        await store.delete("tenant-2")
        assert await store.tenants() == ["tenant-1"] and await store.shared_secret("nope") is None
    anyio.run(go)
    raw = path.read_bytes()
    assert SECRET.encode() not in raw and b"tenant-1" not in raw and path.stat().st_mode & 0o077 == 0
    other = EncryptedFileConnectStore(path, EncryptedFileConnectStore.generate_key())
    from cryptography.fernet import InvalidToken
    with pytest.raises(InvalidToken):
        anyio.run(other.shared_secret, "tenant-1")                                       # the wrong key cannot read it


async def test_dm_04_end_to_end_verify():
    store = MemoryConnectStore()
    await store.put("tenant-1", SECRET)
    ep = ConnectWebhookReceiver(MemoryLocalQueue(), store)

    def make_site(post):
        async def on_issue(key):
            post.headers["Authorization"] = f"JWT {token()}"
            await post(jira_body(key, "issue_property_set"))
        return SiteSim(on_issue=on_issue)
    report = await run_stack(verify_dm_04, endpoint=ep, make_site=make_site, config=EventPlaneConfig(probe_issue="SBX-4"))
    assert report.ok and report.method_id == "DM-04" and timedelta(0) is not None
