"""DM-45: Atlassian Application Tunnels are not a webhook transport: an explicit guard (EV section 44)."""
from __future__ import annotations

import pytest

from atlian_assiant.core.errors import IngressPolicyError, UnsupportedDeliveryPathError
from atlian_assiant.events import guards
from atlian_assiant.events.catalog import Ingress, MethodKind, method

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]


def test_dm_45_catalog_entry_is_a_guard_with_no_components():
    m = method("DM-45")
    assert m.kind is MethodKind.GUARD and m.provisioner is None and m.consumer is None and m.ingress is Ingress.NOT_APPLICABLE


def test_dm_45_refuses_with_a_hint_pointing_at_the_data_center_and_cloud_alternatives():
    with pytest.raises(UnsupportedDeliveryPathError) as exc:
        guards.application_tunnels()
    assert "DM-46/DM-47" in exc.value.hint and "DM-39" in exc.value.hint and "Application Tunnels" in exc.value.message


async def test_dm_45_verifier_confirms_the_guard_refuses():
    report = await guards.verify_dm_45()
    assert report.ok and report.method_id == "DM-45" and "refused as designed" in report.detail


def test_dm_45_related_guards_forge_remote_and_tunnel_under_no_ingress_raise_ingress_policy_errors():
    with pytest.raises(IngressPolicyError) as exc:
        guards.require_ingress("DM-09", Ingress.NONE)
    assert "DM-07" in exc.value.hint
    with pytest.raises(IngressPolicyError) as exc2:
        guards.require_ingress("DM-44", Ingress.NONE)
    assert "DM-17" in exc2.value.hint and "DM-36" in exc2.value.hint
    guards.require_ingress("DM-44", Ingress.TUNNEL)                        # allowed when the policy accepts a tunnel
    guards.require_ingress("DM-07", Ingress.NONE)
    guards.require_ingress("DM-45", Ingress.NONE)                          # guards are n/a


def test_dm_45_other_non_paths_refuse_explicitly():
    for fn, needle in ((guards.mcp_subscription, "DM-48"), (guards.forge_realtime_as_backend_feed, "DM-07")):
        with pytest.raises(UnsupportedDeliveryPathError) as exc:
            fn()
        assert needle in exc.value.hint


def test_dm_45_the_mcp_backend_implements_no_event_source():
    from atlian_assiant.backends.mcp import McpBackend
    assert not hasattr(McpBackend, "events") and not hasattr(McpBackend, "requires_public_ingress") and not hasattr(McpBackend, "method_ids")
