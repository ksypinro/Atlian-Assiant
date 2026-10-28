"""DM-44: reverse tunnel, ngrok / Cloudflare Tunnel (TunnelProvisioner, the local JiraWebhookReceiver behind it, verify_dm_44)."""
from __future__ import annotations

import pytest

from atlian_assiant.core.errors import BackendUnavailableError, ConfigurationError, IngressPolicyError
from atlian_assiant.events.catalog import Ingress, MethodKind, method
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.provision.base import EventProvisioner
from atlian_assiant.events.provision.tunnel import CLASSIFICATION, TunnelHandle, TunnelProvisioner
from atlian_assiant.events.receivers import JiraWebhookReceiver, WebhookUrlPreflight
from atlian_assiant.events.receivers.preflight import ProbeResult
from atlian_assiant.events.stores import MemoryLocalQueue
from atlian_assiant.events.verify import verify_dm_44
from atlian_assiant.policy import CallbackConfirmation, DenyAll

from .sitesim import SiteSim, jira_body, run_stack

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)


def public() -> WebhookUrlPreflight:
    async def resolve(host, port):
        return ["8.8.8.8"]

    async def probe(url):
        return ProbeResult(True, status=401)
    return WebhookUrlPreflight(resolver=resolve, prober=probe)


class Starter:
    def __init__(self, url="https://abc.trycloudflare.com"):
        self.url, self.calls, self.stops = url, [], 0

    def __call__(self, port):
        self.calls.append(port)
        return TunnelHandle(self.url, lambda: setattr(self, "stops", self.stops + 1))


def test_dm_44_catalog_entry_is_a_transport_with_tunnel_ingress():
    m = method("DM-44")
    assert m.kind is MethodKind.TRANSPORT and m.ingress is Ingress.TUNNEL and "DM-44" in JiraWebhookReceiver.method_ids and isinstance(TunnelProvisioner(), EventProvisioner)


async def test_dm_44_provision_starts_the_tunnel_runs_the_preflight_and_prints_the_classification():
    starter = Starter()
    prov = TunnelProvisioner(port=9000, path="/hooks/jira", starter=starter, preflight=public())
    plan = await prov.plan()
    assert plan.method_id == "DM-44" and plan.warnings[:3] == CLASSIFICATION and any("verifier chain" in w for w in plan.warnings)
    out = await prov.apply(plan, confirm=APPROVE)
    assert starter.calls == [9000] and out["callback_url"] == "https://abc.trycloudflare.com/hooks/jira" and "satisfies strict 'no public URL': NO" in out["classification"] and "warnings" not in out
    await prov.teardown(confirm=APPROVE)
    await prov.teardown(confirm=APPROVE)
    assert starter.stops == 1                                                         # idempotent stop


async def test_dm_44_provision_declined_starts_no_tunnel_and_a_failing_preflight_is_reported():
    starter = Starter()
    prov = TunnelProvisioner(starter=starter, preflight=public())
    with pytest.raises(Exception, match="declined"):
        await prov.apply(await prov.plan(), confirm=DenyAll())
    assert starter.calls == []

    async def private(host, port):
        return ["10.0.0.5"]
    bad = TunnelProvisioner(starter=Starter(), preflight=WebhookUrlPreflight(resolver=private))
    assert "not publicly routable" in (await bad.apply(await bad.plan(), confirm=APPROVE))["warnings"]


async def test_dm_44_a_tunnel_is_refused_under_a_strict_no_public_url_policy_with_alternatives():
    with pytest.raises(IngressPolicyError) as exc:
        await TunnelProvisioner(strict_no_public_url=True).plan()
    assert "DM-07" in exc.value.hint and "DM-17" in exc.value.hint


async def test_dm_44_missing_tools_are_reported_with_install_hints():
    with pytest.raises(ConfigurationError, match="ngrok"):
        await (p := TunnelProvisioner(provider="ngrok")).apply(await p.plan(), confirm=APPROVE)
    import atlian_assiant.events.provision.tunnel as t

    class FakeProc:
        stdout = iter(["starting...\n", "no url here\n"])

        def terminate(self):
            self.terminated = True
    original = t.subprocess.Popen
    t.subprocess.Popen = lambda *a, **k: FakeProc()                                   # type: ignore[assignment]
    try:
        with pytest.raises(BackendUnavailableError, match="did not report"):
            t._start_cloudflared("cloudflared", 8080)
        FakeProc.stdout = iter(["INF |  https://quiet-fox-123.trycloudflare.com  |\n"])
        assert t._start_cloudflared("cloudflared", 8080).url == "https://quiet-fox-123.trycloudflare.com"
    finally:
        t.subprocess.Popen = original


async def test_dm_44_end_to_end_verify_through_the_tunnel_to_the_local_receiver():
    ep = JiraWebhookReceiver(MemoryLocalQueue(), method_id="DM-44")

    def make_site(post):
        async def on_issue(key):
            await post(jira_body(key, "issue_property_set"))
        return SiteSim(on_issue=on_issue)
    report = await run_stack(verify_dm_44, endpoint=ep, make_site=make_site, config=EventPlaneConfig(probe_issue="SBX-44"))
    assert report.ok and report.method_id == "DM-44"
