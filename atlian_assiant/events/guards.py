"""Guards for non-paths (design 14.17): every option the research rules out is an explicit, tested refusal with a hint, never a silent no-op."""
from __future__ import annotations

from typing import Any

from ..core.errors import IngressPolicyError, UnsupportedDeliveryPathError
from .catalog import INGRESS_RANK, Ingress, method
from .provision.base import VerificationReport


def application_tunnels() -> None:
    """DM-45: Atlassian Application Tunnels are not a transport for custom webhook receivers (EV section 44)."""
    raise UnsupportedDeliveryPathError("Atlassian Application Tunnels cannot carry custom webhooks to your receiver (they link Cloud and Data Center apps)",
                                       hint="For Data Center in your LAN use DM-46/DM-47 (receiver inside the network); for Cloud use a relay (DM-39..43) or a pull method.")


def upstream_event_feed(kind: str = "Kafka") -> None:
    """DM-49: Atlassian offers no general Kafka/AMQP/SSE feed of Jira/Confluence events (EV section 48)."""
    raise UnsupportedDeliveryPathError(f"there is no upstream {kind} feed of Jira/Confluence events",
                                       hint="Use any event source (webhook relay, Forge inbox, polling) and publish downstream with KafkaSink, RedisStreamSink, SseBroadcaster or WebSocketBroadcaster.")


def mcp_subscription() -> None:
    """MCP v2 is not an event-subscription API (EV section 47): the MCP backend implements no EventSource."""
    raise UnsupportedDeliveryPathError("the MCP server has no subscription API and the MCP backend implements no EventSource",
                                       hint="Poll through MCP search tools instead: JqlPollingSource/CqlPollingSource with transport=BackendKind.MCP (DM-48).")


def forge_realtime_as_backend_feed() -> None:
    """Forge Realtime is a UI feature (EV section 16): it cannot feed a backend."""
    raise UnsupportedDeliveryPathError("Forge Realtime only reaches the UI of a Forge app; it is not a backend event feed",
                                       hint="Use the Forge inbox (DM-07) for backends; DM-10 is for live UI panels.")


def require_ingress(method_id: str, max_ingress: Ingress) -> None:
    """Forcing a method that needs more exposure than the policy allows raises ``IngressPolicyError`` (Forge Remote or a tunnel under 'no ingress')."""
    m = method(method_id)
    if m.ingress is Ingress.NOT_APPLICABLE:
        return
    if INGRESS_RANK[m.ingress] > INGRESS_RANK[max_ingress]:
        alternatives = {"DM-09": "DM-07 (Forge inbox)", "DM-44": "DM-07, DM-17, DM-19 or DM-36 (pull methods)"}.get(method_id, "a method with ingress 'none' (see events_list_methods max_ingress=none)")
        raise IngressPolicyError(f"{method_id} ({m.name}) needs {m.ingress.value} ingress but the policy allows at most {max_ingress.value}", hint=f"Use {alternatives}.")


def _refusal_report(method_id: str, fn: Any, *args: Any) -> VerificationReport:
    try:
        fn(*args)
    except (UnsupportedDeliveryPathError, IngressPolicyError) as exc:
        ok = bool(exc.hint)
        return VerificationReport(method_id, ok, None, f"refused as designed: {exc.message}" if ok else "refused WITHOUT a hint")
    return VerificationReport(method_id, False, None, "the guard did not refuse: a research non-path would silently do nothing")


async def verify_dm_45(**_: Any) -> VerificationReport:
    """DM-45 verifier: the guard must refuse, with a hint."""
    return _refusal_report("DM-45", application_tunnels)


async def verify_dm_49(**_: Any) -> VerificationReport:
    """DM-49 verifier: the upstream-feed guard must refuse, with a hint (downstream sinks are tested separately)."""
    return _refusal_report("DM-49", upstream_event_feed)
