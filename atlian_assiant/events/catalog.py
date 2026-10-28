"""Delivery-method catalog (design 14.2, 14.19): every way of being notified is one numbered entry; CI fails on unmapped research sections."""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from enum import StrEnum

from ..capabilities import Product


class Ingress(StrEnum):
    NONE = "none"
    LAN_ONLY = "lan_only"
    MANAGED = "managed"
    TUNNEL = "tunnel"
    REQUIRED = "required"
    NOT_APPLICABLE = "n/a"


INGRESS_RANK = {Ingress.NONE: 0, Ingress.LAN_ONLY: 1, Ingress.MANAGED: 2, Ingress.TUNNEL: 3, Ingress.REQUIRED: 4, Ingress.NOT_APPLICABLE: 0}


class MethodKind(StrEnum):
    MACHINE_PUSH = "machine_push"
    MACHINE_PULL = "machine_pull"
    IN_ATLASSIAN = "in_atlassian"
    LIVE_UI = "live_ui"
    HUMAN = "human"
    TRANSPORT = "transport"
    GUARD = "guard"


@dataclass(frozen=True, slots=True)
class DeliveryMethod:
    id: str                              # "DM-03"
    name: str
    research_refs: tuple[str, ...]       # ("EV §4.3", "EV §6")
    kind: MethodKind
    products: frozenset[Product]
    ingress: Ingress
    provisioner: str | None              # "module:Class"; None only when nothing needs setting up
    consumer: str | None                 # "module:Class"; None only for GUARD
    verifier: str                        # "module:function" end-to-end probe or guard test
    phase: str
    preview: bool = False
    legacy: bool = False
    entitlements: tuple[str, ...] = ()

    @property
    def implemented(self) -> bool:
        """True once the method's phase has landed (see ``IMPLEMENTED_PHASES``)."""
        return self.phase in IMPLEMENTED_PHASES


@dataclass(frozen=True, slots=True)
class CrossCuttingRule:
    id: str                              # "CC-07"
    name: str
    research_refs: tuple[str, ...]
    implemented_by: tuple[str, ...]      # "module:Class" paths
    phase: str = "P3"

    @property
    def implemented(self) -> bool:
        return self.phase in IMPLEMENTED_PHASES


#: Phases whose catalog entries must be importable and have a contract test. Each later phase adds itself when it lands;
#: until then the entries are *planned* (research is mapped, components are not yet claimed). See DCR-003.
IMPLEMENTED_PHASES: frozenset[str] = frozenset({"P3", "P4", "P4b", "P4c"})

JIRA_FAMILY = frozenset({Product.JIRA, Product.JIRA_SOFTWARE})
JIRA_AND_CONFLUENCE = frozenset({Product.JIRA, Product.JIRA_SOFTWARE, Product.JSM, Product.CONFLUENCE})

DELIVERY_METHODS: tuple[DeliveryMethod, ...] = (
    DeliveryMethod(
        "DM-01", 'Jira webhook registered in the admin UI', ('EV §3', 'EV §4.1'), MethodKind.MACHINE_PUSH,
        JIRA_FAMILY, Ingress.REQUIRED,
        "atlian_assiant.events.provision.jira_webhooks:JiraAdminWebhookProvisioner",
        "atlian_assiant.events.receivers.jira:JiraWebhookReceiver",
        "atlian_assiant.events.verify:verify_dm_01", "P3",
    ),
    DeliveryMethod(
        "DM-02", 'Jira admin webhook REST /rest/webhooks/1.0/webhook', ('EV §4.2', 'EV §5'), MethodKind.MACHINE_PUSH,
        JIRA_FAMILY, Ingress.REQUIRED,
        "atlian_assiant.events.provision.jira_webhooks:JiraAdminWebhookProvisioner",
        "atlian_assiant.events.receivers.jira:JiraWebhookReceiver",
        "atlian_assiant.events.verify:verify_dm_02", "P3",
    ),
    DeliveryMethod(
        "DM-03", 'Jira dynamic webhook REST /rest/api/3/webhook (OAuth 2.0 / Connect)', ('EV §4.3', 'EV §6'), MethodKind.MACHINE_PUSH,
        JIRA_FAMILY, Ingress.REQUIRED,
        "atlian_assiant.events.provision.jira_webhooks:JiraDynamicWebhookProvisioner",
        "atlian_assiant.events.receivers.jira:JiraWebhookReceiver",
        "atlian_assiant.events.verify:verify_dm_03", "P3",
    ),
    DeliveryMethod(
        "DM-04", 'Connect webhook module (legacy / migration)', ('EV §4.4', 'EV §46'), MethodKind.MACHINE_PUSH,
        JIRA_AND_CONFLUENCE, Ingress.REQUIRED,
        "atlian_assiant.events.provision.connect:ConnectAppProvisioner",
        "atlian_assiant.events.receivers.connect:ConnectWebhookReceiver",
        "atlian_assiant.events.verify:verify_dm_04", "P4c", legacy=True,
    ),
    DeliveryMethod(
        "DM-05", 'Failed-webhook replay GET /rest/api/3/webhook/failed', ('EV §7.4',), MethodKind.MACHINE_PULL,
        JIRA_FAMILY, Ingress.NONE,
        None,
        "atlian_assiant.events.sources.failed_replay:WebhookFailedReplaySource",
        "atlian_assiant.events.verify:verify_dm_05", "P3",
    ),
    DeliveryMethod(
        "DM-06", 'Forge triggers processed entirely inside Forge', ('EV §1', 'EV §8', 'EV §9', 'EV §10'), MethodKind.IN_ATLASSIAN,
        JIRA_AND_CONFLUENCE, Ingress.NONE,
        "atlian_assiant.events.provision.forge:ForgeAppProvisioner",
        "atlian_assiant.events.provision.forge:ForgeLogVerifier",
        "atlian_assiant.events.verify:verify_dm_06", "P4",
    ),
    DeliveryMethod(
        "DM-07", 'Forge triggers → Async Queue → Storage inbox → App REST pull', ('EV §13', 'EV §14', 'EV §15', 'EV §38'), MethodKind.MACHINE_PULL,
        JIRA_AND_CONFLUENCE, Ingress.NONE,
        "atlian_assiant.events.provision.forge:ForgeAppProvisioner",
        "atlian_assiant.events.sources.forge:ForgeInboxSource",
        "atlian_assiant.events.verify:verify_dm_07", "P4", preview=True,
    ),
    DeliveryMethod(
        "DM-08", 'Forge Dynamic Triggers (runtime subscriptions)', ('EV §12',), MethodKind.MACHINE_PULL,
        JIRA_AND_CONFLUENCE, Ingress.NONE,
        "atlian_assiant.events.provision.forge:ForgeSubscriptionManager",
        "atlian_assiant.events.sources.forge:ForgeInboxSource",
        "atlian_assiant.events.verify:verify_dm_08", "P4", preview=True,
    ),
    DeliveryMethod(
        "DM-09", 'Forge Remote', ('EV §17',), MethodKind.MACHINE_PUSH,
        JIRA_AND_CONFLUENCE, Ingress.REQUIRED,
        "atlian_assiant.events.provision.forge:ForgeAppProvisioner",
        "atlian_assiant.events.receivers.forge:ForgeRemoteReceiver",
        "atlian_assiant.events.verify:verify_dm_09", "P4",
    ),
    DeliveryMethod(
        "DM-10", 'Forge Realtime live UI', ('EV §16',), MethodKind.LIVE_UI,
        JIRA_AND_CONFLUENCE, Ingress.NONE,
        "atlian_assiant.events.provision.forge:ForgeAppProvisioner",
        "atlian_assiant.events.provision.forge:ForgeRealtimePanel",
        "atlian_assiant.events.verify:verify_dm_10", "P4",
    ),
    DeliveryMethod(
        "DM-11", 'Org Events API /events, /events/{id}, /event-actions', ('EV §18',), MethodKind.MACHINE_PULL,
        frozenset({Product.PLATFORM}), Ingress.NONE,
        "atlian_assiant.events.provision.org_audit:OrgAuditProvisioner",
        "atlian_assiant.events.sources.org_audit:OrgEventsListSource",
        "atlian_assiant.events.verify:verify_dm_11", "P4", entitlements=('Organization admin API key',),
    ),
    DeliveryMethod(
        "DM-12", 'Org /events-stream cursor polling', ('EV §19', 'EV §41'), MethodKind.MACHINE_PULL,
        frozenset({Product.PLATFORM}), Ingress.NONE,
        "atlian_assiant.events.provision.org_audit:OrgAuditProvisioner",
        "atlian_assiant.events.sources.org_audit:OrgEventsStreamSource",
        "atlian_assiant.events.verify:verify_dm_12", "P4", entitlements=('Organization admin API key',),
    ),
    DeliveryMethod(
        "DM-13", 'Org audit-log webhooks', ('EV §20',), MethodKind.MACHINE_PUSH,
        frozenset({Product.PLATFORM}), Ingress.REQUIRED,
        "atlian_assiant.events.provision.org_audit:AuditWebhookProvisioner",
        "atlian_assiant.events.receivers.audit:AuditWebhookReceiver",
        "atlian_assiant.events.verify:verify_dm_13", "P4", entitlements=('Atlassian Guard audit-log webhooks entitlement',),
    ),
    DeliveryMethod(
        "DM-14", 'Audit exclusions & completeness', ('EV §21',), MethodKind.MACHINE_PULL,
        frozenset({Product.PLATFORM}), Ingress.NOT_APPLICABLE,
        "atlian_assiant.events.provision.org_audit:AuditCompletenessCheck",
        "atlian_assiant.events.provision.org_audit:AuditGapReport",
        "atlian_assiant.events.verify:verify_dm_14", "P4", entitlements=('Atlassian Guard',),
    ),
    DeliveryMethod(
        "DM-15", 'Jira Automation → Send web request', ('EV §22', 'EV §26'), MethodKind.MACHINE_PUSH,
        JIRA_FAMILY, Ingress.REQUIRED,
        "atlian_assiant.events.automation:AutomationRuleProvisioner",
        "atlian_assiant.events.receivers.automation:AutomationWebRequestReceiver",
        "atlian_assiant.events.verify:verify_dm_15", "P3",
    ),
    DeliveryMethod(
        "DM-16", 'Confluence Automation → Send web request', ('EV §27',), MethodKind.MACHINE_PUSH,
        frozenset({Product.CONFLUENCE}), Ingress.REQUIRED,
        "atlian_assiant.events.automation:AutomationRuleProvisioner",
        "atlian_assiant.events.receivers.automation:AutomationWebRequestReceiver",
        "atlian_assiant.events.verify:verify_dm_16", "P3",
    ),
    DeliveryMethod(
        "DM-17", 'Jira Automation → Amazon SNS → SQS', ('EV §23', 'EV §40', 'EV §57'), MethodKind.TRANSPORT,
        JIRA_FAMILY, Ingress.NONE,
        "atlian_assiant.events.automation:AutomationRuleProvisioner",
        "atlian_assiant.events.sources.sqs:SqsSource",
        "atlian_assiant.events.verify:verify_dm_17", "P4",
    ),
    DeliveryMethod(
        "DM-18", 'Jira Automation → AWS SSM', ('EV §24',), MethodKind.TRANSPORT,
        JIRA_FAMILY, Ingress.NONE,
        "atlian_assiant.events.automation:AutomationRuleProvisioner",
        "atlian_assiant.events.sources.ssm:SsmDocumentAdapter",
        "atlian_assiant.events.verify:verify_dm_18", "P4",
    ),
    DeliveryMethod(
        "DM-19", 'Jira Edge Connector (JEC)', ('EV §25', 'EV §39', 'EV §58'), MethodKind.MACHINE_PULL,
        frozenset({Product.JSM}), Ingress.NONE,
        "atlian_assiant.events.automation:AutomationRuleProvisioner",
        "atlian_assiant.events.sources.jec:JecScriptAdapter",
        "atlian_assiant.events.verify:verify_dm_19", "P4", entitlements=('JSM Premium or Enterprise',),
    ),
    DeliveryMethod(
        "DM-20", 'Automation → email', ('EV §22', 'EV §27'), MethodKind.HUMAN,
        JIRA_AND_CONFLUENCE, Ingress.NONE,
        "atlian_assiant.events.automation:AutomationRuleProvisioner",
        "atlian_assiant.events.sources.imap:ImapSource",
        "atlian_assiant.events.verify:verify_dm_20", "P4b",
    ),
    DeliveryMethod(
        "DM-21", 'Automation → Slack', ('EV §22', 'EV §27'), MethodKind.HUMAN,
        JIRA_AND_CONFLUENCE, Ingress.NONE,
        "atlian_assiant.events.automation:AutomationRuleProvisioner",
        "atlian_assiant.events.sources.slack:SlackSocketModeSource",
        "atlian_assiant.events.verify:verify_dm_21", "P4b",
    ),
    DeliveryMethod(
        "DM-22", 'Automation → Microsoft Teams', ('EV §22', 'EV §27'), MethodKind.HUMAN,
        JIRA_AND_CONFLUENCE, Ingress.NONE,
        "atlian_assiant.events.automation:AutomationRuleProvisioner",
        "atlian_assiant.events.sources.teams:TeamsChannelPollingSource",
        "atlian_assiant.events.verify:verify_dm_22", "P4b",
    ),
    DeliveryMethod(
        "DM-23", 'Automation → Twilio SMS', ('EV §22', 'EV §27'), MethodKind.HUMAN,
        JIRA_AND_CONFLUENCE, Ingress.NONE,
        "atlian_assiant.events.automation:AutomationRuleProvisioner",
        "atlian_assiant.events.sources.twilio:TwilioMessageLogSource",
        "atlian_assiant.events.verify:verify_dm_23", "P4b",
    ),
    DeliveryMethod(
        "DM-24", 'Automation → other connectors, and incoming-webhook trigger', ('EV §22',), MethodKind.MACHINE_PUSH,
        JIRA_AND_CONFLUENCE, Ingress.REQUIRED,
        "atlian_assiant.events.automation:AutomationRuleProvisioner",
        "atlian_assiant.events.automation:PayloadMapping",
        "atlian_assiant.events.verify:verify_dm_24", "P4b",
    ),
    DeliveryMethod(
        "DM-25", 'Jira notification schemes (email)', ('EV §28',), MethodKind.HUMAN,
        JIRA_FAMILY, Ingress.NONE,
        "atlian_assiant.events.provision.jira_notifications:NotificationSchemeProvisioner",
        "atlian_assiant.events.sources.imap:ImapSource",
        "atlian_assiant.events.verify:verify_dm_25", "P4b",
    ),
    DeliveryMethod(
        "DM-26", 'Jira custom workflow event emails', ('EV §28',), MethodKind.HUMAN,
        JIRA_FAMILY, Ingress.NONE,
        "atlian_assiant.events.provision.jira_notifications:CustomEventProvisioner",
        "atlian_assiant.events.sources.imap:ImapSource",
        "atlian_assiant.events.verify:verify_dm_26", "P4b",
    ),
    DeliveryMethod(
        "DM-27", 'Jira watchers', ('EV §28', 'EV §49'), MethodKind.HUMAN,
        JIRA_FAMILY, Ingress.NONE,
        "atlian_assiant.events.provision.jira_notifications:WatchProvisioner",
        "atlian_assiant.events.sources.imap:ImapSource",
        "atlian_assiant.events.verify:verify_dm_27", "P4b",
    ),
    DeliveryMethod(
        "DM-28", 'Confluence page / space / blog watches, immediate + digest email', ('EV §29',), MethodKind.HUMAN,
        frozenset({Product.CONFLUENCE}), Ingress.NONE,
        "atlian_assiant.events.provision.jira_notifications:WatchProvisioner",
        "atlian_assiant.events.sources.imap:ImapSource",
        "atlian_assiant.events.verify:verify_dm_28", "P4b",
    ),
    DeliveryMethod(
        "DM-29", 'Jira saved-filter subscriptions (scheduled email)', ('EV §30',), MethodKind.HUMAN,
        JIRA_AND_CONFLUENCE, Ingress.NONE,
        "atlian_assiant.events.provision.jira_notifications:FilterSubscriptionProvisioner",
        "atlian_assiant.events.sources.imap:ImapSource",
        "atlian_assiant.events.verify:verify_dm_29", "P4b",
    ),
    DeliveryMethod(
        "DM-30", 'Jira RSS (filter / search feeds)', ('EV §30', 'EV §31'), MethodKind.HUMAN,
        JIRA_AND_CONFLUENCE, Ingress.NONE,
        "atlian_assiant.events.provision.rss:RssFeedProvisioner",
        "atlian_assiant.events.sources.rss:RssSource",
        "atlian_assiant.events.verify:verify_dm_30", "P4b",
    ),
    DeliveryMethod(
        "DM-31", 'Confluence RSS (predefined / custom feeds)', ('EV §32',), MethodKind.HUMAN,
        frozenset({Product.CONFLUENCE}), Ingress.NONE,
        "atlian_assiant.events.provision.rss:RssFeedProvisioner",
        "atlian_assiant.events.sources.rss:RssSource",
        "atlian_assiant.events.verify:verify_dm_31", "P4b",
    ),
    DeliveryMethod(
        "DM-32", 'Jira Cloud for Slack (personal + channel)', ('EV §33',), MethodKind.HUMAN,
        JIRA_AND_CONFLUENCE, Ingress.NONE,
        "atlian_assiant.events.provision.slack:SlackChannelProvisioner",
        "atlian_assiant.events.sources.slack:SlackSocketModeSource",
        "atlian_assiant.events.verify:verify_dm_32", "P4b",
    ),
    DeliveryMethod(
        "DM-33", 'Confluence + Slack (personal, page/space subscriptions)', ('EV §33',), MethodKind.HUMAN,
        frozenset({Product.CONFLUENCE}), Ingress.NONE,
        "atlian_assiant.events.provision.slack:SlackChannelProvisioner",
        "atlian_assiant.events.sources.slack:SlackSocketModeSource",
        "atlian_assiant.events.verify:verify_dm_33", "P4b",
    ),
    DeliveryMethod(
        "DM-34", 'Jira for Microsoft Teams (personal + channel)', ('EV §34',), MethodKind.HUMAN,
        JIRA_AND_CONFLUENCE, Ingress.NONE,
        "atlian_assiant.events.provision.teams:TeamsChannelProvisioner",
        "atlian_assiant.events.sources.teams:TeamsChannelPollingSource",
        "atlian_assiant.events.verify:verify_dm_34", "P4b",
    ),
    DeliveryMethod(
        "DM-35", 'JSM incident / on-call notifications', ('EV §49',), MethodKind.HUMAN,
        frozenset({Product.JSM}), Ingress.NONE,
        "atlian_assiant.events.provision.jsm_ops:JsmOpsProvisioner",
        "atlian_assiant.events.sources.jsm_ops:JsmOpsAlertSource",
        "atlian_assiant.events.verify:verify_dm_35", "P4b", entitlements=('JSM Operations (on-call)',),
    ),
    DeliveryMethod(
        "DM-36", 'Jira REST JQL polling', ('EV §35', 'EV §59'), MethodKind.MACHINE_PULL,
        JIRA_FAMILY, Ingress.NONE,
        None,
        "atlian_assiant.events.sources.polling:JqlPollingSource",
        "atlian_assiant.events.verify:verify_dm_36", "P3",
    ),
    DeliveryMethod(
        "DM-37", 'Confluence CQL polling', ('EV §36', 'EV §59'), MethodKind.MACHINE_PULL,
        frozenset({Product.CONFLUENCE}), Ingress.NONE,
        None,
        "atlian_assiant.events.sources.polling:CqlPollingSource",
        "atlian_assiant.events.verify:verify_dm_37", "P3",
    ),
    DeliveryMethod(
        "DM-38", 'Webhook/trigger + periodic reconciliation', ('EV §37',), MethodKind.MACHINE_PULL,
        JIRA_AND_CONFLUENCE, Ingress.NONE,
        None,
        "atlian_assiant.events.sources.reconciler:Reconciler",
        "atlian_assiant.events.verify:verify_dm_38", "P3",
    ),
    DeliveryMethod(
        "DM-39", 'Relay: AWS API Gateway + Lambda → SQS', ('EV §42',), MethodKind.TRANSPORT,
        JIRA_AND_CONFLUENCE, Ingress.MANAGED,
        "atlian_assiant.events.provision.relay:RelayProvisioner",
        "atlian_assiant.events.sources.sqs:SqsSource",
        "atlian_assiant.events.verify:verify_dm_39", "P4",
    ),
    DeliveryMethod(
        "DM-40", 'Relay: Azure Function → Service Bus', ('EV §42',), MethodKind.TRANSPORT,
        JIRA_AND_CONFLUENCE, Ingress.MANAGED,
        "atlian_assiant.events.provision.relay:RelayProvisioner",
        "atlian_assiant.events.sources.relays:AzureServiceBusSource",
        "atlian_assiant.events.verify:verify_dm_40", "P4",
    ),
    DeliveryMethod(
        "DM-41", 'Relay: Google Cloud Run / Function → Pub/Sub', ('EV §42',), MethodKind.TRANSPORT,
        JIRA_AND_CONFLUENCE, Ingress.MANAGED,
        "atlian_assiant.events.provision.relay:RelayProvisioner",
        "atlian_assiant.events.sources.relays:PubSubSource",
        "atlian_assiant.events.verify:verify_dm_41", "P4",
    ),
    DeliveryMethod(
        "DM-42", 'Relay: Cloudflare Worker → Queue', ('EV §42',), MethodKind.TRANSPORT,
        JIRA_AND_CONFLUENCE, Ingress.MANAGED,
        "atlian_assiant.events.provision.relay:RelayProvisioner",
        "atlian_assiant.events.sources.relays:CloudflareQueuePullSource",
        "atlian_assiant.events.verify:verify_dm_42", "P4",
    ),
    DeliveryMethod(
        "DM-43", 'Relay: dedicated webhook relay / broker service', ('EV §42',), MethodKind.TRANSPORT,
        JIRA_AND_CONFLUENCE, Ingress.MANAGED,
        "atlian_assiant.events.provision.relay:RelayProvisioner",
        "atlian_assiant.events.sources.relays:HttpPullBrokerSource",
        "atlian_assiant.events.verify:verify_dm_43", "P4",
    ),
    DeliveryMethod(
        "DM-44", 'Reverse tunnel (ngrok / Cloudflare Tunnel)', ('EV §43',), MethodKind.TRANSPORT,
        JIRA_AND_CONFLUENCE, Ingress.TUNNEL,
        "atlian_assiant.events.provision.tunnel:TunnelProvisioner",
        "atlian_assiant.events.receivers.jira:JiraWebhookReceiver",
        "atlian_assiant.events.verify:verify_dm_44", "P4c",
    ),
    DeliveryMethod(
        "DM-45", 'Atlassian Application Tunnels', ('EV §44',), MethodKind.GUARD,
        JIRA_AND_CONFLUENCE, Ingress.NOT_APPLICABLE,
        None,
        None,
        "atlian_assiant.events.guards:verify_dm_45", "P4c",
    ),
    DeliveryMethod(
        "DM-46", 'Confluence Data Center webhooks', ('EV §45',), MethodKind.MACHINE_PUSH,
        frozenset({Product.CONFLUENCE}), Ingress.LAN_ONLY,
        "atlian_assiant.events.provision.dc_webhooks:ConfluenceDCWebhookProvisioner",
        "atlian_assiant.events.receivers.dc:ConfluenceDCWebhookReceiver",
        "atlian_assiant.events.verify:verify_dm_46", "P4",
    ),
    DeliveryMethod(
        "DM-47", 'Jira Data Center webhooks', ('EV §45',), MethodKind.MACHINE_PUSH,
        JIRA_FAMILY, Ingress.LAN_ONLY,
        "atlian_assiant.events.provision.dc_webhooks:JiraDCWebhookProvisioner",
        "atlian_assiant.events.receivers.dc:JiraDCWebhookReceiver",
        "atlian_assiant.events.verify:verify_dm_47", "P4",
    ),
    DeliveryMethod(
        "DM-48", 'MCP v2 (not a subscription API): polling through MCP tools', ('EV §47',), MethodKind.MACHINE_PULL,
        JIRA_AND_CONFLUENCE, Ingress.NONE,
        None,
        "atlian_assiant.events.sources.polling:JqlPollingSource",
        "atlian_assiant.events.verify:verify_dm_48", "P4c",
    ),
    DeliveryMethod(
        "DM-49", 'Kafka / AMQP / SSE feed (absent upstream)', ('EV §48',), MethodKind.GUARD,
        JIRA_AND_CONFLUENCE, Ingress.NOT_APPLICABLE,
        None,
        "atlian_assiant.events.sinks.kafka:KafkaSink",
        "atlian_assiant.events.guards:verify_dm_49", "P4c",
    ),
)

CROSS_CUTTING: tuple[CrossCuttingRule, ...] = (
    CrossCuttingRule("CC-01", 'Event-system taxonomy (webhook vs polling feed vs Forge vs queue)', ('EV §1', 'EV §2'),
                     ('atlian_assiant.events.catalog:DeliveryMethod',), "P3"),
    CrossCuttingRule("CC-02", 'Full Jira webhook event catalog, including version, system-option, sprint and board events', ('EV §5',),
                     ('atlian_assiant.events.normalize:JIRA_WEBHOOK_EVENTS',), "P3"),
    CrossCuttingRule("CC-03", 'Delivery semantics: retries, `X-Atlassian-Webhook-Identifier`, `-Retry`, `-Flow`, failed API', ('EV §7',),
                     ('atlian_assiant.events.receivers.base:QueueEndpoint', 'atlian_assiant.events.model:DeliveryInfo'), "P3"),
    CrossCuttingRule("CC-04", 'Forge Jira / Jira Software / JSM / Confluence event coverage', ('EV §9', 'EV §10'),
                     ('atlian_assiant.events.normalize:FORGE_NOUNS',), "P3"),
    CrossCuttingRule("CC-05", 'Cascading-delete gap', ('EV §11',),
                     ('atlian_assiant.events.enrich:CanonicalEnricher',), "P3"),
    CrossCuttingRule("CC-06", 'Human vs machine comparison tables', ('EV §49', 'EV §50'),
                     ('atlian_assiant.events.catalog:methods_table',), "P4c"),
    CrossCuttingRule("CC-07", 'Event payload is not the canonical database', ('EV §51',),
                     ('atlian_assiant.events.enrich:CanonicalEnricher',), "P3"),
    CrossCuttingRule("CC-08", 'Idempotency', ('EV §52',),
                     ('atlian_assiant.events.stores.base:DedupStore',), "P3"),
    CrossCuttingRule("CC-09", 'Ordering', ('EV §53',),
                     ('atlian_assiant.events.enrich:CanonicalEnricher',), "P3"),
    CrossCuttingRule("CC-10", 'Public webhook security', ('EV §54',),
                     ('atlian_assiant.events.receivers.base:HmacSignatureVerifier', 'atlian_assiant.events.receivers.preflight:WebhookUrlPreflight'), "P3"),
    CrossCuttingRule("CC-11", 'Private / pull security', ('EV §55',),
                     ('atlian_assiant.events.stores.base:LocalQueue',), "P3"),
    CrossCuttingRule("CC-12", 'Recommended architectures', ('EV §38', 'EV §39', 'EV §40', 'EV §41', 'EV §56', 'EV §57', 'EV §58', 'EV §59', 'EV §63'),
                     ('atlian_assiant.events.planner:Blueprint',), "P4"),
    CrossCuttingRule("CC-13", 'Decision guide', ('EV §60',),
                     ('atlian_assiant.events.planner:EventPlaneFactory',), "P4"),
    CrossCuttingRule("CC-14", 'Suggested PoCs A–D', ('EV §61',),
                     ('atlian_assiant.events.poc:PocHarness',), "P4"),
    CrossCuttingRule("CC-15", 'Official documentation registry', ('EV §62',),
                     ('atlian_assiant.events.references:REFERENCES',), "P3"),
)


def method(method_id: str) -> DeliveryMethod:
    """Look up one delivery method by id (``"DM-03"``)."""
    for m in DELIVERY_METHODS:
        if m.id == method_id:
            return m
    raise KeyError(f"unknown delivery method {method_id!r}; ids run DM-01 .. DM-49")


def resolve(path: str) -> object:
    """Import ``module:attr`` (raises ImportError / AttributeError when the component does not exist)."""
    module, _, attr = path.partition(":")
    return getattr(importlib.import_module(module), attr)


def methods_table(*, only_implemented: bool = False) -> str:
    """Markdown comparison of all delivery methods (machine and human channels): the generated CC-06 table."""
    lines = ["| ID | Method | Kind | Ingress | Phase | Status |", "|---|---|---|---|---|---|"]
    for m in DELIVERY_METHODS:
        if only_implemented and not m.implemented:
            continue
        flags = ", ".join(f for f, on in (("preview", m.preview), ("legacy", m.legacy)) if on)
        lines.append(f"| {m.id} | {m.name}{f' ({flags})' if flags else ''} | {m.kind.value} | {m.ingress.value} | {m.phase} | "
                     f"{'implemented' if m.implemented else 'planned'} |")
    return "\n".join(lines)
