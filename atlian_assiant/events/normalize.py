"""Normalization tables (design section 14.11): native event -> (entity, action). Unknown events are kept as UNKNOWN/OTHER."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..capabilities import Product
from .model import ChangeAction, ChangeEvent, DeliveryInfo, EntityType, EventSourceKind

E_, A_ = EntityType, ChangeAction


def _crud(entity: EntityType, prefix: str, extra: Mapping[str, ChangeAction] | None = None,
          verbs: Iterable[str] = ("created", "updated", "deleted")) -> dict[str, tuple[EntityType, ChangeAction]]:
    base = {"created": A_.CREATED, "updated": A_.UPDATED, "deleted": A_.DELETED}
    table = {f"{prefix}_{v}": (entity, base[v]) for v in verbs}
    table.update({f"{prefix}_{k}": (entity, v) for k, v in (extra or {}).items()})
    return table


JIRA_WEBHOOK_EVENTS: dict[str, tuple[EntityType, ChangeAction]] = {
    **_crud(E_.ISSUE, "issue"),
    **_crud(E_.COMMENT, "comment"), **_crud(E_.WORKLOG, "worklog"),
    **_crud(E_.ATTACHMENT, "attachment", verbs=("created", "deleted")),
    **_crud(E_.ISSUE_LINK, "issuelink", verbs=("created", "deleted")),
    "issue_property_set": (E_.ISSUE_PROPERTY, A_.UPDATED), "issue_property_deleted": (E_.ISSUE_PROPERTY, A_.DELETED),
    **_crud(E_.ISSUE_TYPE, "issuetype"),
    **_crud(E_.PROJECT, "project", {"soft_deleted": A_.SOFT_DELETED, "restored_deleted": A_.RESTORED, "archived": A_.ARCHIVED,
                                    "restored_archived": A_.RESTORED}),
    **_crud(E_.VERSION, "version", {"released": A_.RELEASED, "unreleased": A_.UNRELEASED, "moved": A_.MOVED, "merged": A_.MERGED}),
    **_crud(E_.FILTER, "filter"), **_crud(E_.USER, "user"),
    **{name: (E_.CONFIG, A_.CONFIG_CHANGED) for name in (
        "option_voting_changed", "option_watching_changed", "option_unassigned_issues_changed", "option_subtasks_changed",
        "option_attachments_changed", "option_issuelinks_changed", "option_timetracking_changed")},
    **_crud(E_.SPRINT, "sprint", {"started": A_.STARTED, "closed": A_.CLOSED}),
    **_crud(E_.BOARD, "board", {"configuration_changed": A_.CONFIG_CHANGED}),
    "jira_expression_evaluation_failed": (E_.CONFIG, A_.OTHER),
}

FORGE_NOUNS: dict[str, EntityType] = {
    "issue": E_.ISSUE, "comment": E_.COMMENT, "worklog": E_.WORKLOG, "attachment": E_.ATTACHMENT, "issuelink": E_.ISSUE_LINK,
    "project": E_.PROJECT, "version": E_.VERSION, "user": E_.USER, "group": E_.GROUP, "sprint": E_.SPRINT, "board": E_.BOARD,
    "request": E_.REQUEST, "page": E_.PAGE, "blogpost": E_.BLOGPOST, "whiteboard": E_.WHITEBOARD, "database": E_.DATABASE,
    "folder": E_.FOLDER, "smartlink": E_.SMART_LINK, "space": E_.SPACE, "label": E_.LABEL, "search": E_.UNKNOWN,
    "attachments": E_.ATTACHMENT, "comments": E_.COMMENT,
}
FORGE_VERBS: dict[str, ChangeAction] = {
    "created": A_.CREATED, "updated": A_.UPDATED, "deleted": A_.DELETED, "assigned": A_.ASSIGNED, "viewed": A_.VIEWED,
    "mentioned": A_.MENTIONED, "moved": A_.MOVED, "copied": A_.COPIED, "added": A_.ADDED, "removed": A_.REMOVED,
    "started": A_.STARTED, "closed": A_.CLOSED, "performed": A_.PERFORMED, "archived": A_.ARCHIVED, "restored": A_.RESTORED,
    "trashed": A_.SOFT_DELETED,
}
CONFLUENCE_DC_EVENTS: dict[str, tuple[EntityType, ChangeAction]] = {
    **{f"{n}_{v}": (E_.PAGE if n == "page" else E_.BLOGPOST if n == "blog" else E_.SPACE if n == "space" else E_.COMMENT if n == "comment" else E_.ATTACHMENT,
                   FORGE_VERBS.get(v, A_.OTHER))
       for n in ("page", "blog", "space", "comment", "attachment") for v in ("created", "updated", "removed", "restored", "moved", "viewed")},
}
AUTOMATION_TRIGGER_ACTIONS: dict[str, ChangeAction] = {
    "issue_created": A_.CREATED, "issue_transitioned": A_.UPDATED, "comment_added": A_.CREATED, "field_changed": A_.UPDATED,
    "page_published": A_.CREATED, "page_updated": A_.UPDATED, "comment_created": A_.CREATED, "scheduled": A_.PERFORMED,
    "incoming_webhook": A_.PERFORMED,
}


def classify_jira_event(native: str) -> tuple[EntityType, ChangeAction]:
    """Entity and action of a Jira webhook event name; ``(UNKNOWN, OTHER)`` for anything not in the table."""
    key = native.removeprefix("jira:")
    return JIRA_WEBHOOK_EVENTS.get(key, (E_.UNKNOWN, A_.OTHER))


def classify_forge_event(native: str) -> tuple[EntityType, ChangeAction]:
    """``avi:jira:created:issue`` -> (ISSUE, CREATED); unknown nouns/verbs degrade to UNKNOWN/OTHER."""
    parts = native.split(":")
    if len(parts) < 4 or parts[0] != "avi":
        return E_.UNKNOWN, A_.OTHER
    return FORGE_NOUNS.get(parts[3], E_.UNKNOWN), FORGE_VERBS.get(parts[2], A_.OTHER)


def classify_confluence_dc_event(native: str) -> tuple[EntityType, ChangeAction]:
    return CONFLUENCE_DC_EVENTS.get(native, (E_.UNKNOWN, A_.OTHER))


def _ms_to_dt(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _hash(body: bytes | str) -> str:
    raw = body if isinstance(body, bytes) else body.encode()
    return hashlib.sha256(raw).hexdigest()[:32]


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    return next((v for k, v in headers.items() if k.lower() == lowered), None)


def delivery_from_headers(headers: Mapping[str, str], *, method_id: str, body_len: int, verified: bool) -> DeliveryInfo:
    retry = _header(headers, "X-Atlassian-Webhook-Retry")
    return DeliveryInfo(method_id=method_id, attempt=int(retry) if retry and retry.isdigit() else 0,
                        flow=_header(headers, "X-Atlassian-Webhook-Flow"), transport_id=_header(headers, "X-Atlassian-Webhook-Identifier"),
                        verified=verified, payload_bytes=body_len)


def normalize_jira_webhook(payload: Mapping[str, Any], headers: Mapping[str, str], *, method_id: str, body: bytes,
                           received_at: datetime | None = None, verified: bool = False,
                           source: EventSourceKind = EventSourceKind.JIRA_WEBHOOK) -> ChangeEvent:
    """Jira Cloud / admin webhook body -> ChangeEvent. The dedup id is ``X-Atlassian-Webhook-Identifier`` (else a body hash)."""
    native = str(payload.get("webhookEvent") or payload.get("eventType") or "unknown")
    entity, action = classify_jira_event(native)
    delivery = delivery_from_headers(headers, method_id=method_id, body_len=len(body), verified=verified)
    issue: Mapping[str, Any] = payload.get("issue") or {}
    fields: Mapping[str, Any] = issue.get("fields") or {}
    entity_id = entity_key = container = version = None
    if issue and entity in (E_.ISSUE, E_.COMMENT, E_.WORKLOG, E_.ATTACHMENT, E_.ISSUE_LINK, E_.ISSUE_PROPERTY):
        entity_id, entity_key = _s(issue.get("id")), _s(issue.get("key"))
        version, container = _s(fields.get("updated")), (fields.get("project") or {}).get("key")
        if entity in (E_.COMMENT, E_.WORKLOG, E_.ATTACHMENT):
            sub = payload.get(entity.value) or payload.get("attachment") or {}
            entity_id = _s(sub.get("id")) or entity_id
    elif entity is E_.PROJECT:
        proj = payload.get("project") or {}
        entity_id, entity_key = _s(proj.get("id")), _s(proj.get("key"))
        container = entity_key
    elif entity in (E_.VERSION, E_.SPRINT, E_.BOARD, E_.FILTER, E_.USER, E_.ISSUE_TYPE):
        sub = payload.get(entity.value) or payload.get("issueType") or {}
        entity_id = _s(sub.get("id") or sub.get("accountId"))
        entity_key = _s(sub.get("name") or sub.get("key"))
    changed = [str(i.get("field")) for i in (payload.get("changelog") or {}).get("items", []) if i.get("field")]
    actor = (payload.get("user") or {}).get("accountId") or (payload.get("comment") or {}).get("author", {}).get("accountId")
    event_id = delivery.transport_id or _hash(body)
    return ChangeEvent(event_id=event_id, source=source, product=Product.JIRA, entity_type=entity, action=action, native_type=native,
                       entity_id=entity_id, entity_key=entity_key, container_key=container, entity_version=version,
                       occurred_at=_ms_to_dt(payload.get("timestamp")), received_at=received_at or datetime.now(UTC),
                       actor_account_id=_s(actor), changed_fields=changed, delivery=delivery, payload=dict(payload))


def _s(value: Any) -> str | None:
    return None if value is None else str(value)


def normalize_automation_envelope(payload: Mapping[str, Any], headers: Mapping[str, str], *, method_id: str, body: bytes,
                                  received_at: datetime | None = None, verified: bool = False) -> ChangeEvent:
    """``STANDARD_ENVELOPE`` (design 14.7) -> ChangeEvent. The dedup id is ``atl_event_id`` (else a body hash)."""
    product = Product.CONFLUENCE if payload.get("product") == "confluence" else Product.JIRA
    try:
        entity = EntityType(str(payload.get("entity_type", "unknown")))
    except ValueError:
        entity = EntityType.UNKNOWN
    trigger = str(payload.get("trigger", ""))
    action = AUTOMATION_TRIGGER_ACTIONS.get(trigger, A_.OTHER)
    delivery = DeliveryInfo(method_id=method_id, verified=verified, payload_bytes=len(body), transport_id=_header(headers, "X-Atlassian-Webhook-Identifier"))
    return ChangeEvent(event_id=str(payload.get("atl_event_id") or _hash(body)), source=EventSourceKind.AUTOMATION_WEB_REQUEST, product=product,
                       entity_type=entity, action=action, native_type=f"automation:{trigger or 'unknown'}",
                       entity_id=_s(payload.get("entity_id")), entity_key=_s(payload.get("entity_key")), entity_version=_s(payload.get("version")),
                       occurred_at=_ms_to_dt(payload.get("occurred_at")), received_at=received_at or datetime.now(UTC),
                       actor_account_id=_s(payload.get("actor")), delivery=delivery, payload=dict(payload))


def poll_hit_event(*, source: EventSourceKind, product: Product, entity: EntityType, entity_id: str, version: str, key: str | None,
                   container: str | None, created: bool, method_id: str, backend: Any = None,
                   received_at: datetime | None = None) -> ChangeEvent:
    """A JQL/CQL poll hit (any transport) -> ChangeEvent with the ``id:version`` dedup key (design 14.4)."""
    return ChangeEvent(event_id=f"{entity_id}:{version}", source=source, product=product, entity_type=entity,
                       action=A_.CREATED if created else A_.UPDATED, native_type=f"poll:{entity.value}", entity_id=entity_id, entity_key=key,
                       container_key=container, entity_version=version, received_at=received_at or datetime.now(UTC),
                       delivery=DeliveryInfo(method_id=method_id, transport_backend=backend, verified=True))


# ---------------------------------------------------------------------------------------------------------------------
# Human-channel references (email, Slack, Teams, SMS, RSS): AtlassianLinkParser
# ---------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class AtlassianReference:
    product: Product
    entity_type: EntityType
    key: str | None = None
    entity_id: str | None = None
    url: str | None = None


_BROWSE = re.compile(r"https?://[\w.-]+/(?:jira/software/projects/[A-Z][A-Z0-9_]+/(?:boards/\d+/)?)?browse/([A-Z][A-Z0-9_]+-\d+)")
_PORTAL = re.compile(r"https?://[\w.-]+/servicedesk/customer/portal/\d+/([A-Z][A-Z0-9_]+-\d+)")
_PAGE = re.compile(r"https?://[\w.-]+/wiki/spaces/([\w~.-]+)/(?:pages|blog)/(\d+)")
_PAGEID = re.compile(r"https?://[\w.-]+/wiki/[^\s]*?[?&]pageId=(\d+)")
_KEY = re.compile(r"\b([A-Z][A-Z0-9_]{1,9})-(\d+)\b")


class AtlassianLinkParser:
    """Extracts Atlassian references from free text. Bare keys are accepted only for the known project keys."""

    def __init__(self, project_keys: Iterable[str] = ()) -> None:
        self.project_keys = frozenset(project_keys)

    def parse(self, text: str) -> list[AtlassianReference]:
        found: dict[tuple[str, str], AtlassianReference] = {}
        for m in _PORTAL.finditer(text):
            found.setdefault(("request", m.group(1)), AtlassianReference(Product.JSM, E_.REQUEST, key=m.group(1), url=m.group(0)))
        for m in _BROWSE.finditer(text):
            found.setdefault(("issue", m.group(1)), AtlassianReference(Product.JIRA, E_.ISSUE, key=m.group(1), url=m.group(0)))
        for m in _PAGE.finditer(text):
            found.setdefault(("page", m.group(2)), AtlassianReference(Product.CONFLUENCE, E_.PAGE, entity_id=m.group(2), url=m.group(0)))
        for m in _PAGEID.finditer(text):
            found.setdefault(("page", m.group(1)), AtlassianReference(Product.CONFLUENCE, E_.PAGE, entity_id=m.group(1), url=m.group(0)))
        for m in _KEY.finditer(text):
            key = f"{m.group(1)}-{m.group(2)}"
            if m.group(1) in self.project_keys and ("issue", key) not in found and ("request", key) not in found:
                found[("issue", key)] = AtlassianReference(Product.JIRA, E_.ISSUE, key=key)
        return list(found.values())

    def events(self, text: str, *, source: EventSourceKind, native_type: str, message_id: str, method_id: str,
               received_at: datetime | None = None) -> list[ChangeEvent]:
        """One NOTIFIED event per reference (digests split per entity); one UNKNOWN event when nothing resolves, so nothing is dropped."""
        when = received_at or datetime.now(UTC)
        refs = self.parse(text)
        delivery = DeliveryInfo(method_id=method_id, transport_id=message_id, verified=False)
        if not refs:
            return [ChangeEvent(event_id=message_id, source=source, product=Product.PLATFORM, entity_type=E_.UNKNOWN, action=A_.NOTIFIED,
                                native_type=native_type, received_at=when, delivery=delivery, payload={"text": text[:2000]})]
        many = len(refs) > 1
        return [ChangeEvent(event_id=f"{message_id}#{r.key or r.entity_id}" if many else message_id, source=source, product=r.product,
                            entity_type=r.entity_type, action=A_.NOTIFIED, native_type=native_type, entity_id=r.entity_id, entity_key=r.key,
                            received_at=when, delivery=delivery, payload={"url": r.url, "text": text[:2000]}) for r in refs]


def dumps_event(event: ChangeEvent) -> str:
    return json.dumps(json.loads(event.model_dump_json()), sort_keys=True)


def audit_change_event(audit: Any, *, source: EventSourceKind, method_id: str, received_at: datetime | None = None, verified: bool = True,
                       transport_id: str | None = None) -> ChangeEvent:
    """An organization audit event (list, stream or webhook) -> ChangeEvent(AUDIT, PERFORMED). The dedup id is the audit event id (duplicates keep it)."""
    return ChangeEvent(event_id=str(audit.id), source=source, product=Product.PLATFORM, entity_type=EntityType.AUDIT, action=ChangeAction.PERFORMED,
                       native_type=str(audit.action or "audit"), entity_id=str(audit.id), occurred_at=audit.occurred_at, processed_at=audit.processed_at,
                       received_at=received_at or datetime.now(UTC), actor_account_id=audit.actor_id,
                       delivery=DeliveryInfo(method_id=method_id, transport_id=transport_id or str(audit.id), verified=verified),
                       payload={"context": audit.context, "attributes": audit.attributes})


_FORGE_PRODUCTS = {"jira": Product.JIRA, "jira-software": Product.JIRA_SOFTWARE, "jira-service-management": Product.JSM, "confluence": Product.CONFLUENCE}


def forge_change_event(record: Mapping[str, Any], *, source: EventSourceKind, method_id: str, received_at: datetime | None = None, verified: bool = True) -> ChangeEvent:
    """A Forge inbox record ``{id, source, eventType, entity, receivedAt, processed}`` (EV section 14) or a Remote invocation body -> ChangeEvent."""
    native = str(record.get("eventType") or "unknown")
    entity, action = classify_forge_event(native)
    parts = native.split(":")
    product = _FORGE_PRODUCTS.get(parts[1], Product.PLATFORM) if len(parts) > 1 else Product.PLATFORM
    ent = record.get("entity") or {}
    rid = record.get("id") or record.get("key") or _hash(json.dumps(record, sort_keys=True, default=str))
    millis = record.get("receivedAt")
    return ChangeEvent(event_id=str(rid), source=source, product=product, entity_type=entity, action=action, native_type=native,
                       entity_id=_s(ent.get("id")), entity_key=_s(ent.get("key")), occurred_at=_ms_to_dt(millis), received_at=received_at or datetime.now(UTC),
                       delivery=DeliveryInfo(method_id=method_id, transport_id=str(rid), verified=verified), payload=dict(record))
