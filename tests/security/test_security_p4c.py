"""P4c security control (design section 17): SEC-16 Connect (legacy): per-tenant shared secrets encrypted, qsh validation, uninstall wipes."""
from __future__ import annotations

import json
import time

import jwt
import pytest

from atlian_assiant.events.receivers import InboundRequest, connect_qsh
from atlian_assiant.events.receivers.connect import (
    ConnectLifecycleEndpoint,
    ConnectWebhookReceiver,
    EncryptedFileConnectStore,
)
from atlian_assiant.events.stores import MemoryLocalQueue

pytestmark = [pytest.mark.layer_11, pytest.mark.anyio]
SECRET = "t" * 32


def tok(path, secret=SECRET, query=None, iss="tenant-1", qsh=True):
    claims = {"iss": iss, "exp": time.time() + 60}
    if qsh:
        claims["qsh"] = connect_qsh("POST", path, query or {})
    return jwt.encode(claims, secret, algorithm="HS256")


async def test_sec_16_shared_secrets_are_stored_encrypted_in_an_owner_only_file(tmp_path):
    store = EncryptedFileConnectStore(tmp_path / "s.enc", EncryptedFileConnectStore.generate_key())
    await ConnectLifecycleEndpoint(store).handle(InboundRequest("POST", "/connect/installed", {}, json.dumps({"clientKey": "tenant-1", "sharedSecret": SECRET}).encode()))
    raw = (tmp_path / "s.enc").read_bytes()
    assert SECRET.encode() not in raw and b"tenant-1" not in raw and (tmp_path / "s.enc").stat().st_mode & 0o077 == 0
    assert await store.shared_secret("tenant-1") == SECRET


async def test_sec_16_qsh_binds_a_token_to_one_request_so_it_cannot_be_replayed_elsewhere(tmp_path):
    store = EncryptedFileConnectStore(tmp_path / "s.enc", EncryptedFileConnectStore.generate_key())
    await store.put("tenant-1", SECRET)
    ep = ConnectWebhookReceiver(MemoryLocalQueue(), store)
    body = b'{"webhookEvent":"jira:issue_updated"}'
    good = InboundRequest("POST", "/hooks/connect", {"Authorization": f"JWT {tok('/hooks/connect')}", "X-Atlassian-Webhook-Identifier": "a"}, body)
    replay = InboundRequest("POST", "/hooks/connect", {"Authorization": f"JWT {tok('/hooks/other')}", "X-Atlassian-Webhook-Identifier": "b"}, body)         # minted for another path
    no_qsh = InboundRequest("POST", "/hooks/connect", {"Authorization": f"JWT {tok('/hooks/connect', qsh=False)}", "X-Atlassian-Webhook-Identifier": "c"}, body)
    assert [(await ep.handle(r)).status for r in (good, replay)] == [202, 401] and (await ep.handle(no_qsh)).status in (202, 401)


async def test_sec_16_uninstall_wipes_the_secret_and_later_deliveries_fail(tmp_path):
    store = EncryptedFileConnectStore(tmp_path / "s.enc", EncryptedFileConnectStore.generate_key())
    await store.put("tenant-1", SECRET)
    life = ConnectLifecycleEndpoint(store)
    hook = ConnectWebhookReceiver(MemoryLocalQueue(), store)
    req = InboundRequest("POST", "/hooks/connect", {"Authorization": f"JWT {tok('/hooks/connect')}", "X-Atlassian-Webhook-Identifier": "a"}, b'{"webhookEvent":"jira:issue_updated"}')
    assert (await hook.handle(req)).status == 202
    uninstall = InboundRequest("POST", "/connect/uninstalled", {"Authorization": f"JWT {tok('/connect/uninstalled')}"}, json.dumps({"clientKey": "tenant-1"}).encode())
    assert (await life.handle(uninstall)).status == 204 and await store.tenants() == []
    again = InboundRequest("POST", "/hooks/connect", {"Authorization": f"JWT {tok('/hooks/connect')}", "X-Atlassian-Webhook-Identifier": "z"}, b'{"webhookEvent":"jira:issue_updated"}')
    assert (await hook.handle(again)).status == 401
    assert b"tenant-1" not in (tmp_path / "s.enc").read_bytes()
