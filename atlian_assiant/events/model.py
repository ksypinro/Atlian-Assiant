"""Normalized event model (design section 14.1). Every delivery method produces ``ChangeEvent``s."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from ..capabilities import BackendKind, Product


class EventSourceKind(StrEnum):
    JIRA_WEBHOOK = "jira_webhook"                     # DM-01/02/03 (admin UI, admin REST, dynamic)
    CONNECT_WEBHOOK = "connect_webhook"               # DM-04
    JIRA_DC_WEBHOOK = "jira_dc_webhook"               # DM-47
    CONFLUENCE_DC_WEBHOOK = "confluence_dc_webhook"   # DM-46
    WEBHOOK_FAILED_REPLAY = "webhook_failed_replay"   # DM-05
    FORGE_INBOX = "forge_inbox"                       # DM-07/08
    FORGE_REMOTE = "forge_remote"                     # DM-09
    ORG_EVENTS_LIST = "org_events_list"               # DM-11
    ORG_EVENTS_STREAM = "org_events_stream"           # DM-12
    AUDIT_WEBHOOK = "audit_webhook"                   # DM-13
    AUTOMATION_WEB_REQUEST = "automation_web_request"  # DM-15/16
    AWS_SQS = "aws_sqs"                               # DM-17/39
    AWS_SSM = "aws_ssm"                               # DM-18
    JEC = "jec"                                       # DM-19
    EMAIL_IMAP = "email_imap"                         # DM-20/25/26/27/28/29
    SLACK_SOCKET_MODE = "slack_socket_mode"           # DM-21/32/33
    TEAMS_GRAPH = "teams_graph"                       # DM-22/34
    TWILIO_LOG = "twilio_log"                         # DM-23
    RSS = "rss"                                       # DM-30/31
    JSM_OPS_ALERTS = "jsm_ops_alerts"                 # DM-35
    JQL_POLL = "jql_poll"                             # DM-36 (+ DM-48 via MCP transport)
    CQL_POLL = "cql_poll"                             # DM-37 (+ DM-48 via MCP transport)
    RECONCILER = "reconciler"                         # DM-38
    AZURE_SERVICE_BUS = "azure_service_bus"           # DM-40
    GCP_PUBSUB = "gcp_pubsub"                         # DM-41
    CLOUDFLARE_QUEUE = "cloudflare_queue"             # DM-42
    HTTP_PULL_BROKER = "http_pull_broker"             # DM-43


class EntityType(StrEnum):
    ISSUE = "issue"; COMMENT = "comment"; WORKLOG = "worklog"; ATTACHMENT = "attachment"  # noqa: E702
    ISSUE_LINK = "issue_link"; ISSUE_PROPERTY = "issue_property"; ISSUE_TYPE = "issue_type"  # noqa: E702
    PROJECT = "project"; VERSION = "version"; FILTER = "filter"; USER = "user"; GROUP = "group"  # noqa: E702
    SPRINT = "sprint"; BOARD = "board"; CONFIG = "config"; REQUEST = "request"; ALERT = "alert"  # noqa: E702
    PAGE = "page"; BLOGPOST = "blogpost"; WHITEBOARD = "whiteboard"; DATABASE = "database"  # noqa: E702
    FOLDER = "folder"; SMART_LINK = "smart_link"; CUSTOM_CONTENT = "custom_content"; SPACE = "space"  # noqa: E702
    LABEL = "label"; TASK = "task"; AUDIT = "audit"; UNKNOWN = "unknown"  # noqa: E702


class ChangeAction(StrEnum):
    CREATED = "created"; UPDATED = "updated"; DELETED = "deleted"; SOFT_DELETED = "soft_deleted"  # noqa: E702
    RESTORED = "restored"; ARCHIVED = "archived"; MOVED = "moved"; COPIED = "copied"; VIEWED = "viewed"  # noqa: E702
    STARTED = "started"; CLOSED = "closed"; RELEASED = "released"; UNRELEASED = "unreleased"  # noqa: E702
    MERGED = "merged"; ASSIGNED = "assigned"; MENTIONED = "mentioned"; ADDED = "added"  # noqa: E702
    REMOVED = "removed"; PERFORMED = "performed"; CONFIG_CHANGED = "config_changed"  # noqa: E702
    NOTIFIED = "notified"; OTHER = "other"  # noqa: E702


class DeliveryInfo(BaseModel):
    method_id: str | None = None           # "DM-03": which delivery method produced it
    attempt: int = 0                       # X-Atlassian-Webhook-Retry
    flow: str | None = None                # X-Atlassian-Webhook-Flow: Primary | Secondary
    transport_id: str | None = None        # webhook identifier, SQS MessageId, inbox id, Slack ts, Message-ID
    transport_backend: BackendKind | None = None   # for pollers: rest | mcp | acli | twg | rest_dc
    verified: bool = False                 # signature / token verification passed
    payload_bytes: int | None = None       # recorded for PoC metrics (EV section 61)


class ChangeEvent(BaseModel):
    """One normalized change notification. ``payload`` is untrusted; ``canonical`` is filled by the enricher."""

    event_id: str                          # stable id used for delivery dedup
    source: EventSourceKind
    product: Product
    entity_type: EntityType
    action: ChangeAction
    native_type: str                       # "jira:issue_updated", "avi:confluence:updated:page", audit action, "slack:message"
    entity_id: str | None = None
    entity_key: str | None = None          # "PROJ-123"
    container_key: str | None = None       # project key / space key
    entity_version: str | None = None      # issue `updated` / Confluence version number
    occurred_at: datetime | None = None
    received_at: datetime
    processed_at: datetime | None = None   # org events-stream `processedAt`
    actor_account_id: str | None = None
    cloud_id: str | None = None
    changed_fields: list[str] = Field(default_factory=list)
    delivery: DeliveryInfo = Field(default_factory=DeliveryInfo)
    payload: dict[str, Any] = Field(default_factory=dict, repr=False)   # untrusted; kept for audit
    canonical: Any | None = None           # filled by the enricher: Issue / Content / Tombstone

    @property
    def dedup_key(self) -> str:
        return f"{self.source}:{self.event_id}"
