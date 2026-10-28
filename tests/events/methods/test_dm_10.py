"""DM-10: Forge Realtime live UI (scaffold with a SCOPED channel; verifier needs the browser backend)."""
from __future__ import annotations

import pytest

from atlian_assiant.backends.forge import ForgeAppSpec, ForgeCliRunner, generate
from atlian_assiant.core.errors import UnsupportedDeliveryPathError, UnsupportedOperationError
from atlian_assiant.events.catalog import Ingress, MethodKind, method
from atlian_assiant.events.provision.forge import ForgeAppProvisioner, ForgeRealtimePanel
from atlian_assiant.events.verify import verify_dm_10
from atlian_assiant.policy import CallbackConfirmation

from .forgesim import FakeForgeCli

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]


def test_dm_10_catalog_entry():
    m = method("DM-10")
    assert m.kind is MethodKind.LIVE_UI and m.ingress is Ingress.NONE and m.consumer.endswith("ForgeRealtimePanel")


async def test_dm_10_provision_scaffolds_a_scoped_panel_for_issue_and_page(tmp_path):
    for panel, module in (("issue", "jira:issuePanel"), ("page", "confluence:contentBylineItem")):
        prov = ForgeAppProvisioner(ForgeAppSpec(mode="inbox", realtime_panel=panel), directory=tmp_path / panel, cli=ForgeCliRunner(which=lambda b: None))
        out = await prov.apply(await prov.plan(), confirm=CallbackConfirmation(lambda p: True))
        assert module in (tmp_path / panel / "manifest.yml").read_text() and "changes-${contextId}" in (tmp_path / panel / "src/panel.js").read_text() and out["files"]


def test_dm_10_global_channels_are_refused_with_the_authorization_hint():
    with pytest.raises(UnsupportedDeliveryPathError) as exc:
        generate(ForgeAppSpec(realtime_panel="issue", realtime_global=True))
    assert "EV section 16" in exc.value.hint


async def test_dm_10_verifier_explains_that_the_browser_backend_is_needed_until_p5():
    report = await verify_dm_10(panel=ForgeRealtimePanel())
    assert not report.ok and report.method_id == "DM-10" and "browser backend" in report.detail


async def test_dm_10_verifier_delegates_to_a_browser_backend_when_one_exists():
    from atlian_assiant.events.provision.base import VerificationReport

    class Browser:
        async def assert_live_update(self):
            return VerificationReport("DM-10", True, 1.2, "panel updated live")
    assert (await verify_dm_10(panel=ForgeRealtimePanel(Browser()))).ok


def test_dm_10_has_no_python_consumer():
    with pytest.raises(UnsupportedOperationError, match="DM-07"):
        ForgeRealtimePanel().scaffold_only()
    assert FakeForgeCli
