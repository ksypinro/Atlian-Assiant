"""DM-09: Forge Remote (ForgeAppProvisioner(remote), ForgeRemoteReceiver with FIT verification, verify_dm_09)."""
from __future__ import annotations

import json
import time
from datetime import timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from atlian_assiant.backends.forge import ForgeAppSpec, ForgeCliRunner
from atlian_assiant.core.errors import InvalidRequestError
from atlian_assiant.events.catalog import Ingress, method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.model import ChangeAction, EntityType, EventSourceKind
from atlian_assiant.events.provision.forge import ForgeAppProvisioner
from atlian_assiant.events.receivers import InboundRequest
from atlian_assiant.events.receivers.forge import ForgeRemoteReceiver
from atlian_assiant.events.stores import MemoryLocalQueue
from atlian_assiant.events.verify import verify_dm_09
from atlian_assiant.policy import CallbackConfirmation

from .forgesim import FakeForgeCli
from .sitesim import Post, SiteSim, run_stack

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def fit(aud: str = "app-1", exp: float | None = None, key=KEY) -> str:
    return jwt.encode({"aud": aud, "exp": exp or time.time() + 60}, key, algorithm="RS256")


def receiver(queue=None) -> ForgeRemoteReceiver:
    return ForgeRemoteReceiver(queue or MemoryLocalQueue(), app_id="app-1", key_source=lambda _: KEY.public_key())


def body(n: int = 1, event_type: str = "avi:jira:updated:issue") -> bytes:
    return json.dumps({"eventType": event_type, "event": {"id": f"f{n}", "issue": {"id": "10001", "key": "PROJ-1"}}, "installContext": "ari:x"}).encode()


def req(b: bytes, token: str | None) -> InboundRequest:
    return InboundRequest("POST", "/hooks/forge", {"Authorization": f"Bearer {token}"} if token else {}, b)


def test_dm_09_catalog_entry():
    m = method("DM-09")
    assert m.ingress is Ingress.REQUIRED and m.consumer.endswith("ForgeRemoteReceiver") and "DM-09" in ForgeRemoteReceiver.method_ids


async def test_dm_09_provision_generates_the_remote_app_and_needs_a_public_https_backend(tmp_path):
    spec = ForgeAppSpec(mode="remote", remote_endpoint="https://py.example.com", products=("jira",))
    prov = ForgeAppProvisioner(spec, directory=tmp_path / "app", site="example.atlassian.net", cli=ForgeCliRunner(runner=FakeForgeCli(), which=lambda b: "/f"))
    plan = await prov.plan()
    assert plan.method_id == "DM-09"
    await prov.apply(plan, confirm=APPROVE)
    assert "invokeRemote" in (tmp_path / "app/src/events.js").read_text() and "py.example.com" in (tmp_path / "app/manifest.yml").read_text()
    with pytest.raises(InvalidRequestError, match="DM-07"):
        await ForgeAppProvisioner(ForgeAppSpec(mode="remote"), directory=tmp_path / "x").plan()


async def test_dm_09_consumer_rejects_missing_wrong_audience_expired_and_forged_tokens():
    q = MemoryLocalQueue()
    ep = receiver(q)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    statuses = [(await ep.handle(req(body(), t))).status for t in (None, fit("app-2"), fit(exp=time.time() - 5), fit(key=other), "garbage")]
    assert statuses == [401] * 5 and await q.depth() == 0


async def test_dm_09_consumer_accepts_a_valid_fit_normalizes_and_dedups_on_the_forge_event_id():
    q = MemoryLocalQueue()
    ep = receiver(q)
    assert [(await ep.handle(req(body(1), fit()))).status for _ in range(2)] == [202, 200]
    ev = (await q.get())[1]
    assert (ev.event_id, ev.entity_type, ev.action, ev.entity_key, ev.source, ev.delivery.verified) == ("f1", EntityType.ISSUE, ChangeAction.UPDATED, "PROJ-1",
                                                                                                          EventSourceKind.FORGE_REMOTE, True)
    assert ev.delivery.method_id == "DM-09"


async def test_dm_09_events_without_an_id_get_a_stable_hash_id():
    q = MemoryLocalQueue()
    ep = receiver(q)
    raw = json.dumps({"eventType": "avi:confluence:created:page", "event": {"content": {"id": "5"}}}).encode()
    assert [(await ep.handle(req(raw, fit()))).status for _ in range(2)] == [202, 200]


async def test_dm_09_end_to_end_verify_over_the_remote_path():
    ep = receiver()

    def make_site(post: Post):
        async def on_issue(key):
            post.headers["Authorization"] = f"Bearer {fit()}"
            await post({"eventType": "avi:jira:updated:issue", "event": {"id": f"p-{key}", "issue": {"id": "1", "key": key}}})
        return SiteSim(on_issue=on_issue)
    report = await run_stack(verify_dm_09, endpoint=ep, make_site=make_site, config=EventPlaneConfig(probe_issue="SBX-9"))
    assert report.ok and report.method_id == "DM-09"
    assert (await verify_dm_09(client=SiteSim(), config=EventPlaneConfig(), inbox=__import__("atlian_assiant.events.verify", fromlist=["x"]).EventInbox(),
                               timeout=timedelta(milliseconds=20))).ok is False
