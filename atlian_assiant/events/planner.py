"""Blueprints and the planner (design 14.16, EV sections 38-41, 56-60, 63): requirements -> recommended delivery methods."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from typing import Any

from ..capabilities import Deployment, Product
from ..core.errors import InvalidRequestError
from .catalog import INGRESS_RANK, Ingress


class Blueprint(StrEnum):
    DIRECT_WEBHOOK = "direct_webhook"            # EV section 60          DM-02/03 + DM-05
    MANAGED_RELAY = "managed_relay"              # EV section 42          DM-03 + DM-39..43
    TUNNEL = "tunnel"                            # EV section 43          DM-03 + DM-44
    FORGE_NATIVE = "forge_native"                # EV section 1 option 1  DM-06
    FORGE_INBOX = "forge_inbox"                  # EV sections 38, 56     DM-07 (+ DM-08)
    FORGE_REMOTE = "forge_remote"                # EV section 17          DM-09
    JEC = "jec"                                  # EV sections 39, 58     DM-19
    AWS_QUEUE = "aws_queue"                      # EV sections 40, 57     DM-17
    AWS_SSM = "aws_ssm"                          # EV section 24          DM-18
    ORG_AUDIT = "org_audit"                      # EV section 41          DM-12 (+ DM-11, DM-14)
    AUDIT_WEBHOOK = "audit_webhook"              # EV section 20          DM-13 (+ DM-14)
    ZERO_APP_POLLING = "zero_app_polling"        # EV section 59          DM-36/37
    MCP_POLLING = "mcp_polling"                  # EV section 47          DM-48
    DATA_CENTER_WEBHOOK = "dc_webhook"           # EV section 45          DM-46/47
    LIVE_FORGE_UI = "live_forge_ui"              # EV sections 16, 60     DM-10
    HUMAN_NOTIFICATIONS = "human_notifications"  # EV sections 28-34, 49  DM-20..35


HUMAN_CHANNEL_METHODS: dict[str, tuple[str, ...]] = {
    "email": ("DM-20", "DM-25", "DM-26", "DM-27", "DM-28", "DM-29"),
    "slack": ("DM-21", "DM-32", "DM-33"),
    "teams": ("DM-22", "DM-34"),
    "sms": ("DM-23",),
    "rss": ("DM-30", "DM-31"),
    "on_call": ("DM-35",),
}
RELAY_BY_CLOUD = {"aws": "DM-39", "azure": "DM-40", "gcp": "DM-41", "cloudflare": "DM-42", "broker": "DM-43"}


@dataclass(frozen=True)
class EventRequirements:
    products: frozenset[Product]
    deployment: Deployment = Deployment.CLOUD
    max_ingress: Ingress = Ingress.NONE         # most exposure accepted: NONE < LAN_ONLY < MANAGED < TUNNEL < REQUIRED
    must_process_in_lan: bool = True
    processing_inside_atlassian_ok: bool = False
    forge_allowed: bool = True
    preview_features_allowed: bool = False
    jsm_premium_or_enterprise: bool = False
    clouds: frozenset[str] = frozenset()        # subset of RELAY_BY_CLOUD keys
    mcp_only_credentials: bool = False
    need_audit_events: bool = False
    audit_webhook_entitled: bool = False
    human_channels: frozenset[str] = frozenset()   # subset of HUMAN_CHANNEL_METHODS keys
    live_forge_ui: bool = False
    max_latency: timedelta = timedelta(minutes=5)


@dataclass(frozen=True)
class EventPlanePlan:
    blueprints: tuple[Blueprint, ...]
    methods: tuple[str, ...]                 # DM ids for the fast path
    reconciliation: tuple[str, ...]          # DM ids for the correctness path
    rationale: tuple[str, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)


class EventPlaneFactory:
    @staticmethod
    def recommend(req: EventRequirements) -> EventPlanePlan:
        """Follow the EV section 60 decision guide. Always adds the reconciler (DM-38): polling and webhooks both miss deletes."""
        bps: list[Blueprint] = []
        dms: list[str] = []
        rec: list[str] = ["DM-38"]
        why: list[str] = []
        warn: list[str] = []

        def allows(level: Ingress) -> bool:
            return INGRESS_RANK[req.max_ingress] >= INGRESS_RANK[level]

        jira = bool(req.products & {Product.JIRA, Product.JIRA_SOFTWARE, Product.JSM})
        conf = Product.CONFLUENCE in req.products
        relay = next((RELAY_BY_CLOUD[c] for c in RELAY_BY_CLOUD if c in req.clouds), None)
        forge_inbox_ok = req.forge_allowed and req.preview_features_allowed
        forge_native_ok = req.forge_allowed and req.processing_inside_atlassian_ok and not req.must_process_in_lan

        if req.deployment is Deployment.DATA_CENTER:
            bps.append(Blueprint.DATA_CENTER_WEBHOOK)
            dms += ["DM-47"] * jira + ["DM-46"] * conf
            rec += ["DM-36"] * jira + ["DM-37"] * conf
            why.append("Data Center can call receivers inside the same private network (EV section 45).")
            return EventPlanePlan(tuple(bps), tuple(dms), tuple(dict.fromkeys(rec)), tuple(why), tuple(warn))

        if jira:
            if allows(Ingress.REQUIRED):
                bps.append(Blueprint.DIRECT_WEBHOOK)
                dms += ["DM-03", "DM-05"]
                why.append("Public receiver allowed: direct Jira webhook is lowest latency (EV section 60).")
            elif allows(Ingress.MANAGED) and relay:
                bps.append(Blueprint.MANAGED_RELAY)
                dms += ["DM-03", relay]
                why.append("Webhook terminates on a managed relay; the LAN worker pulls (EV section 42).")
            elif allows(Ingress.TUNNEL):
                bps.append(Blueprint.TUNNEL)
                dms += ["DM-03", "DM-44"]
                warn.append("A tunnel still creates a public URL; not strict 'no public URL' (EV section 43).")
            elif Product.JSM in req.products and req.jsm_premium_or_enterprise and req.must_process_in_lan:
                bps.append(Blueprint.JEC)
                dms.append("DM-19")
                why.append("JSM Premium/Enterprise: JEC targets systems not reachable from the Internet (EV sections 25, 60).")
            elif forge_inbox_ok:
                bps.append(Blueprint.FORGE_INBOX)
                dms.append("DM-07")
                why.append("Forge triggers -> inbox -> outbound pull (EV sections 38, 60).")
            elif forge_native_ok:
                bps.append(Blueprint.FORGE_NATIVE)
                dms.append("DM-06")
                why.append("Processing may run inside Atlassian; nothing leaves Forge (EV section 1).")
            elif "aws" in req.clouds:
                bps.append(Blueprint.AWS_QUEUE)
                dms.append("DM-17")
                why.append("Automation -> SNS -> SQS: durable, no inbound listener (EV sections 23, 60).")
            elif req.mcp_only_credentials:
                bps.append(Blueprint.MCP_POLLING)
                dms.append("DM-48")
                why.append("Only MCP credentials: poll through MCP search tools (EV section 47).")
            else:
                bps.append(Blueprint.ZERO_APP_POLLING)
                dms.append("DM-36")
                why.append("Least platform complexity: JQL polling with checkpoints (EV sections 59, 63).")
            rec.append("DM-36")

        if conf:
            if "DM-07" in dms or "DM-06" in dms:
                pass
            elif forge_inbox_ok:
                bps.append(Blueprint.FORGE_INBOX)
                dms.append("DM-07")
                why.append("Confluence Cloud has no REST webhook API; Forge is the event path (EV section 46).")
            elif forge_native_ok:
                bps.append(Blueprint.FORGE_NATIVE)
                dms.append("DM-06")
            elif req.forge_allowed and allows(Ingress.REQUIRED):
                bps.append(Blueprint.FORGE_REMOTE)
                dms.append("DM-09")
                why.append("Forge triggers invoke the Python backend via Forge Remote (EV section 17).")
            elif allows(Ingress.MANAGED) and relay:
                bps.append(Blueprint.MANAGED_RELAY)
                dms += ["DM-16", relay]
                why.append("Confluence Automation web request into a managed relay (EV sections 27, 42).")
            elif req.mcp_only_credentials:
                bps.append(Blueprint.MCP_POLLING)
                dms.append("DM-48")
            else:
                bps.append(Blueprint.ZERO_APP_POLLING)
                dms.append("DM-37")
                why.append("Confluence via CQL polling (EV sections 36, 59).")
            rec.append("DM-37")

        if req.need_audit_events:
            if req.audit_webhook_entitled and allows(Ingress.REQUIRED):
                bps.append(Blueprint.AUDIT_WEBHOOK)
                dms.append("DM-13")
                rec.append("DM-11")
            else:
                bps.append(Blueprint.ORG_AUDIT)
                dms.append("DM-12")
                why.append("Audit/security events via cursor-based /events-stream (EV sections 41, 60).")
            dms.append("DM-14")
            warn.append("Audit completeness depends on entitlements and exclusions (EV section 21).")

        if req.live_forge_ui:
            if not req.forge_allowed:
                warn.append("Live Forge UI requested but Forge is not allowed.")
            else:
                bps.append(Blueprint.LIVE_FORGE_UI)
                dms.append("DM-10")

        if req.human_channels:
            bps.append(Blueprint.HUMAN_NOTIFICATIONS)
            for channel in sorted(req.human_channels):
                dms += HUMAN_CHANNEL_METHODS[channel]

        if "DM-03" in dms:
            rec.append("DM-05")
            warn.append("Secondary webhook flows can take up to ~15 minutes (EV section 7.3).")
        if {"DM-06", "DM-07", "DM-09"} & set(dms) and req.max_latency < timedelta(minutes=3):
            warn.append("Forge trigger delivery can take up to ~3 minutes (EV section 8.4).")
        if "DM-07" in dms:
            warn.append("Forge App REST API is Preview (EV section 15).")
        return EventPlanePlan(tuple(dict.fromkeys(bps)), tuple(dict.fromkeys(dms)), tuple(dict.fromkeys(rec)), tuple(why), tuple(warn))

    @staticmethod
    def blueprint(name: Blueprint, **params: Any) -> EventPlanePlan:
        """Build ANY blueprint explicitly, even when the planner would not recommend it for the stated requirements.

        Parameters: ``products`` (iterable of Product, default Jira), ``cloud`` (aws|azure|gcp|cloudflare|broker, managed relay), ``channels`` (human channels),
        ``dynamic`` (add DM-08 to the Forge inbox), ``audit_via_webhook``.
        """
        products = frozenset(params.get("products") or {Product.JIRA})
        jira = bool(products & {Product.JIRA, Product.JIRA_SOFTWARE, Product.JSM})
        conf = Product.CONFLUENCE in products
        rec = ["DM-38"] + (["DM-36"] if jira else []) + (["DM-37"] if conf else [])
        why = [f"Explicit blueprint {name.value}: {BLUEPRINT_SUMMARY[name]}"]
        warn: list[str] = []
        m: list[str]
        if name is Blueprint.DIRECT_WEBHOOK:
            m = ["DM-03", "DM-05"]
            warn.append("needs a publicly routable HTTPS receiver (EV section 3.1)")
        elif name is Blueprint.MANAGED_RELAY:
            cloud = str(params.get("cloud", "aws"))
            if cloud not in RELAY_BY_CLOUD:
                raise InvalidRequestError(f"unknown relay cloud {cloud!r}", hint="Use: " + ", ".join(RELAY_BY_CLOUD))
            m = ["DM-03", RELAY_BY_CLOUD[cloud]]
            warn.append("a managed relay is a public URL on a managed service")
        elif name is Blueprint.TUNNEL:
            m = ["DM-03", "DM-44"]
            warn.append("a tunnel creates a public URL; it does not satisfy a strict 'no public URL' policy (EV section 43)")
        elif name is Blueprint.FORGE_NATIVE:
            m = ["DM-06"]
        elif name is Blueprint.FORGE_INBOX:
            m = ["DM-07"] + (["DM-08"] if params.get("dynamic") else [])
            warn.append("Forge App REST API is Preview (EV section 15)")
        elif name is Blueprint.FORGE_REMOTE:
            m = ["DM-09"]
            warn.append("Forge Remote needs a public HTTPS backend (EV section 17)")
        elif name is Blueprint.JEC:
            m = ["DM-19"]
            warn.append("requires JSM Premium or Enterprise (EV section 25)")
        elif name is Blueprint.AWS_QUEUE:
            m = ["DM-17"]
        elif name is Blueprint.AWS_SSM:
            m = ["DM-18"]
        elif name is Blueprint.ORG_AUDIT:
            m, rec = ["DM-12", "DM-11", "DM-14"], []
            warn.append("audit completeness depends on entitlements and exclusions (EV section 21)")
        elif name is Blueprint.AUDIT_WEBHOOK:
            m, rec = ["DM-13", "DM-14"], ["DM-11"]
            warn.append("audit webhooks retry only 3 times and are unordered: the gap reconciler is mandatory (EV section 20)")
        elif name is Blueprint.ZERO_APP_POLLING:
            m = (["DM-36"] if jira else []) + (["DM-37"] if conf else [])
        elif name is Blueprint.MCP_POLLING:
            m = ["DM-48"]
        elif name is Blueprint.DATA_CENTER_WEBHOOK:
            m = (["DM-47"] if jira else []) + (["DM-46"] if conf else [])
        elif name is Blueprint.LIVE_FORGE_UI:
            m = ["DM-10"]
        else:
            channels = sorted(params.get("channels") or HUMAN_CHANNEL_METHODS)
            bad = [c for c in channels if c not in HUMAN_CHANNEL_METHODS]
            if bad:
                raise InvalidRequestError(f"unknown channels {bad}", hint="Use: " + ", ".join(HUMAN_CHANNEL_METHODS))
            m = [d for c in channels for d in HUMAN_CHANNEL_METHODS[c]]
        return EventPlanePlan((name,), tuple(dict.fromkeys(m)), tuple(dict.fromkeys(rec)), tuple(why), tuple(warn))

    @staticmethod
    def build(plan: EventPlanePlan, client: Any, *, store_url: str, config: Any = None) -> Any:
        """Instantiate provisioners + sources + pipeline for a plan. Returns an ``EventPlane`` (``.provision(confirm)``, ``.verify()``, ``.run()``, ``.teardown()``)."""
        from .plane import EventPlane
        return EventPlane.from_plan(plan, client, store_url=store_url, config=config)


BLUEPRINT_SUMMARY: dict[Blueprint, str] = {
    Blueprint.DIRECT_WEBHOOK: "Jira webhook straight to a public receiver, plus failed-delivery replay",
    Blueprint.MANAGED_RELAY: "webhook terminates on a managed relay; the LAN worker pulls",
    Blueprint.TUNNEL: "webhook through a reverse tunnel to a local receiver",
    Blueprint.FORGE_NATIVE: "Forge trigger processed entirely inside Forge",
    Blueprint.FORGE_INBOX: "Forge trigger -> Async Queue -> Storage inbox -> outbound pull",
    Blueprint.FORGE_REMOTE: "Forge trigger invokes your Python backend",
    Blueprint.JEC: "Jira Edge Connector runs your script on the LAN",
    Blueprint.AWS_QUEUE: "Automation -> SNS -> SQS, pulled from the LAN",
    Blueprint.AWS_SSM: "Automation -> SSM Command on managed nodes",
    Blueprint.ORG_AUDIT: "organization audit events through /events-stream",
    Blueprint.AUDIT_WEBHOOK: "organization audit-log webhooks plus a gap reconciler",
    Blueprint.ZERO_APP_POLLING: "JQL/CQL polling with checkpoints and a reconciler",
    Blueprint.MCP_POLLING: "polling through MCP search tools",
    Blueprint.DATA_CENTER_WEBHOOK: "Data Center webhooks to a LAN receiver",
    Blueprint.LIVE_FORGE_UI: "live Forge Realtime panel",
    Blueprint.HUMAN_NOTIFICATIONS: "human notification channels (email, Slack, Teams, SMS, RSS, on-call)",
}
