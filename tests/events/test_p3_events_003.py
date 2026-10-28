"""ChangeEvent, EventSource, normalization tables and the link parser (design 14.1, 14.4, 14.11)."""
from __future__ import annotations

import json

import anyio
import pytest

from atlian_assiant.capabilities import Product
from atlian_assiant.events.model import ChangeAction as A
from atlian_assiant.events.model import ChangeEvent, EventSourceKind
from atlian_assiant.events.model import EntityType as E
from atlian_assiant.events.normalize import (
    JIRA_WEBHOOK_EVENTS,
    AtlassianLinkParser,
    classify_confluence_dc_event,
    classify_forge_event,
    classify_jira_event,
    normalize_automation_envelope,
    normalize_jira_webhook,
    poll_hit_event,
)
from atlian_assiant.events.sources.base import EventSource, LocalQueueSource
from atlian_assiant.events.stores import MemoryLocalQueue

from .conftest import NOW, event

pytestmark = [pytest.mark.layer_11]

#: every Jira webhook event named in EV section 5 and the design 14.11 table
SECTION_5_EVENTS = [
    "jira:issue_created", "jira:issue_updated", "jira:issue_deleted", "issue_property_set", "issue_property_deleted", "worklog_created",
    "worklog_updated", "worklog_deleted", "comment_created", "comment_updated", "comment_deleted", "attachment_created", "attachment_deleted",
    "issuelink_created", "issuelink_deleted", "issuetype_created", "issuetype_updated", "issuetype_deleted", "project_created", "project_updated",
    "project_deleted", "project_soft_deleted", "project_restored_deleted", "project_archived", "project_restored_archived", "jira:version_created",
    "jira:version_updated", "jira:version_released", "jira:version_unreleased", "jira:version_moved", "jira:version_merged", "jira:version_deleted",
    "filter_created", "filter_updated", "filter_deleted", "user_created", "user_updated", "user_deleted", "option_voting_changed",
    "option_watching_changed", "option_unassigned_issues_changed", "option_subtasks_changed", "option_attachments_changed",
    "option_issuelinks_changed", "option_timetracking_changed", "sprint_created", "sprint_updated", "sprint_started", "sprint_closed",
    "sprint_deleted", "board_created", "board_updated", "board_deleted", "board_configuration_changed", "jira_expression_evaluation_failed"]


def test_every_native_event_of_research_section_5_maps_to_a_known_entity_and_action():
    for native in SECTION_5_EVENTS:
        entity, action = classify_jira_event(native)
        assert entity is not E.UNKNOWN and action is not A.OTHER or native == "jira_expression_evaluation_failed", native
    assert classify_jira_event("jira_expression_evaluation_failed") == (E.CONFIG, A.OTHER)
    assert classify_jira_event("jira:issue_updated") == (E.ISSUE, A.UPDATED) and classify_jira_event("issue_updated") == (E.ISSUE, A.UPDATED)
    assert classify_jira_event("project_soft_deleted") == (E.PROJECT, A.SOFT_DELETED) and classify_jira_event("project_restored_archived")[1] is A.RESTORED
    assert classify_jira_event("jira:version_released") == (E.VERSION, A.RELEASED) and classify_jira_event("sprint_started") == (E.SPRINT, A.STARTED)
    assert classify_jira_event("option_voting_changed") == (E.CONFIG, A.CONFIG_CHANGED) and len(JIRA_WEBHOOK_EVENTS) >= len(SECTION_5_EVENTS) - 8


def test_unknown_events_map_to_unknown_other_and_keep_their_native_type():
    assert classify_jira_event("brand_new_event") == (E.UNKNOWN, A.OTHER)
    body = json.dumps({"webhookEvent": "brand_new_event", "timestamp": 1}).encode()
    ev = normalize_jira_webhook(json.loads(body), {"X-Atlassian-Webhook-Identifier": "id-9"}, method_id="DM-03", body=body)
    assert ev.entity_type is E.UNKNOWN and ev.action is A.OTHER and ev.native_type == "brand_new_event" and ev.payload["timestamp"] == 1


def test_forge_and_confluence_dc_events_follow_the_noun_verb_tables():
    assert classify_forge_event("avi:jira:created:issue") == (E.ISSUE, A.CREATED)
    assert classify_forge_event("avi:jira:assigned:issue") == (E.ISSUE, A.ASSIGNED) and classify_forge_event("avi:jira:mentioned:issue")[1] is A.MENTIONED
    assert classify_forge_event("avi:confluence:updated:page") == (E.PAGE, A.UPDATED) and classify_forge_event("avi:confluence:moved:page")[1] is A.MOVED
    assert classify_forge_event("avi:confluence:added:label") == (E.LABEL, A.ADDED) and classify_forge_event("avi:confluence:performed:search")[1] is A.PERFORMED
    assert classify_forge_event("avi:jira:created:sprint")[0] is E.SPRINT and classify_forge_event("avi:jsm:created:request") == (E.REQUEST, A.CREATED)
    assert classify_forge_event("garbage") == (E.UNKNOWN, A.OTHER) and classify_forge_event("avi:jira:teleported:issue")[1] is A.OTHER
    assert classify_confluence_dc_event("page_created") == (E.PAGE, A.CREATED) and classify_confluence_dc_event("blog_created")[0] is E.BLOGPOST
    assert classify_confluence_dc_event("space_updated") == (E.SPACE, A.UPDATED) and classify_confluence_dc_event("nope") == (E.UNKNOWN, A.OTHER)


ISSUE_BODY = {"timestamp": 1788264000000, "webhookEvent": "jira:issue_updated", "user": {"accountId": "acc-1"},
              "issue": {"id": "10001", "key": "PROJ-1", "fields": {"updated": "2026-09-01T12:00:00.000+0000", "project": {"key": "PROJ"}}},
              "changelog": {"items": [{"field": "status"}, {"field": "assignee"}]}}


def test_issue_webhook_normalizes_with_dedup_key_flow_retry_and_changed_fields():
    body = json.dumps(ISSUE_BODY).encode()
    headers = {"x-atlassian-webhook-identifier": "wh-123", "X-Atlassian-Webhook-Retry": "2", "X-Atlassian-Webhook-Flow": "Secondary"}
    ev = normalize_jira_webhook(ISSUE_BODY, headers, method_id="DM-03", body=body, received_at=NOW, verified=True)
    assert (ev.entity_type, ev.action, ev.entity_key, ev.entity_id, ev.container_key) == (E.ISSUE, A.UPDATED, "PROJ-1", "10001", "PROJ")
    assert ev.event_id == "wh-123" and ev.dedup_key == "jira_webhook:wh-123"                      # the identifier is stable across retries
    assert ev.delivery.attempt == 2 and ev.delivery.flow == "Secondary" and ev.delivery.verified and ev.delivery.payload_bytes == len(body)
    assert ev.changed_fields == ["status", "assignee"] and ev.actor_account_id == "acc-1" and ev.entity_version == "2026-09-01T12:00:00.000+0000"
    assert ev.occurred_at is not None and ev.product is Product.JIRA


def test_without_an_identifier_the_dedup_id_is_a_stable_body_hash():
    body = json.dumps(ISSUE_BODY).encode()
    a = normalize_jira_webhook(ISSUE_BODY, {}, method_id="DM-02", body=body)
    b = normalize_jira_webhook(ISSUE_BODY, {}, method_id="DM-02", body=body)
    c = normalize_jira_webhook({**ISSUE_BODY, "webhookEvent": "jira:issue_created"}, {}, method_id="DM-02", body=json.dumps({**ISSUE_BODY, "x": 1}).encode())
    assert a.event_id == b.event_id and a.event_id != c.event_id


def test_comment_project_and_version_webhooks_extract_their_entities():
    comment = normalize_jira_webhook({"webhookEvent": "comment_created", "issue": {"id": "1", "key": "PROJ-2", "fields": {"project": {"key": "PROJ"}}},
                                      "comment": {"id": "55", "author": {"accountId": "acc-9"}}}, {}, method_id="DM-01", body=b"{}")
    assert (comment.entity_type, comment.entity_id, comment.entity_key, comment.actor_account_id) == (E.COMMENT, "55", "PROJ-2", "acc-9")
    project = normalize_jira_webhook({"webhookEvent": "project_archived", "project": {"id": "7", "key": "OPS"}}, {}, method_id="DM-01", body=b"{}")
    assert (project.entity_type, project.action, project.entity_key, project.container_key) == (E.PROJECT, A.ARCHIVED, "OPS", "OPS")
    version = normalize_jira_webhook({"webhookEvent": "jira:version_released", "version": {"id": "3", "name": "1.0"}}, {}, method_id="DM-03", body=b"{}")
    assert (version.entity_type, version.action, version.entity_id, version.entity_key) == (E.VERSION, A.RELEASED, "3", "1.0")


def test_automation_envelope_normalizes_for_jira_and_confluence():
    body = {"v": 1, "atl_event_id": "10001:2026-09-01:rule", "product": "jira", "trigger": "issue_created", "entity_type": "issue", "entity_id": "10001",
            "entity_key": "PROJ-1", "version": "2026-09-01T12:00:00Z", "actor": "acc-1", "occurred_at": "2026-09-01T12:00:00Z"}
    ev = normalize_automation_envelope(body, {}, method_id="DM-15", body=b"{}")
    assert (ev.event_id, ev.entity_type, ev.action, ev.entity_key, ev.native_type, ev.source) == (
        "10001:2026-09-01:rule", E.ISSUE, A.CREATED, "PROJ-1", "automation:issue_created", EventSourceKind.AUTOMATION_WEB_REQUEST)
    page = normalize_automation_envelope({"product": "confluence", "trigger": "page_updated", "entity_type": "page", "entity_id": "123"}, {}, method_id="DM-16", body=b"x")
    assert page.product is Product.CONFLUENCE and page.action is A.UPDATED and page.entity_id == "123" and len(page.event_id) == 32
    odd = normalize_automation_envelope({"entity_type": "spaceship", "trigger": "weird"}, {}, method_id="DM-15", body=b"y")
    assert odd.entity_type is E.UNKNOWN and odd.action is A.OTHER


def test_poll_hit_event_uses_the_id_version_key_and_first_version_means_created():
    ev = poll_hit_event(source=EventSourceKind.JQL_POLL, product=Product.JIRA, entity=E.ISSUE, entity_id="10001", version="v1", key="PROJ-1",
                        container="PROJ", created=True, method_id="DM-36")
    assert ev.event_id == "10001:v1" and ev.action is A.CREATED and ev.delivery.method_id == "DM-36"
    assert poll_hit_event(source=EventSourceKind.CQL_POLL, product=Product.CONFLUENCE, entity=E.PAGE, entity_id="9", version="2", key=None,
                          container=None, created=False, method_id="DM-37").action is A.UPDATED


def test_link_parser_extracts_keys_urls_pages_and_request_keys():
    parser = AtlassianLinkParser(project_keys=["PROJ", "OPS"])
    text = ("Please look at PROJ-12 and https://x.atlassian.net/browse/OPS-7, plus https://x.atlassian.net/wiki/spaces/ENG/pages/4455/Runbook "
            "and https://x.atlassian.net/servicedesk/customer/portal/4/OPS-9. Not WORD-55, and https://x.atlassian.net/wiki/pages/viewpage.action?pageId=777")
    refs = {(r.entity_type, r.key or r.entity_id) for r in parser.parse(text)}
    assert refs == {(E.ISSUE, "PROJ-12"), (E.ISSUE, "OPS-7"), (E.PAGE, "4455"), (E.REQUEST, "OPS-9"), (E.PAGE, "777")}


def test_link_parser_splits_digests_per_entity_and_never_drops_a_message():
    parser = AtlassianLinkParser(project_keys=["PROJ"])
    events = parser.events("PROJ-1 and PROJ-2 changed", source=EventSourceKind.EMAIL_IMAP, native_type="email:jira", message_id="<m1>", method_id="DM-25",
                           received_at=NOW)
    assert [e.entity_key for e in events] == ["PROJ-1", "PROJ-2"] and {e.event_id for e in events} == {"<m1>#PROJ-1", "<m1>#PROJ-2"}
    assert all(e.action is A.NOTIFIED for e in events)
    nothing = parser.events("hello world", source=EventSourceKind.SLACK_SOCKET_MODE, native_type="slack:message", message_id="C1:1.2", method_id="DM-21")
    assert len(nothing) == 1 and nothing[0].entity_type is E.UNKNOWN and nothing[0].payload["text"] == "hello world"


def test_link_parser_ignores_bare_keys_of_unknown_projects():
    assert AtlassianLinkParser().parse("ABC-123 DEF-9") == []


@pytest.mark.anyio
async def test_local_queue_source_is_an_event_source_and_acks_remove_from_the_queue():
    queue = MemoryLocalQueue()
    await queue.put(event("a"))
    await queue.put(event("b"))
    source = LocalQueueSource(queue, poll_interval=0.001)
    assert isinstance(source, EventSource)
    seen: list[ChangeEvent] = []
    async for ev in source.events():
        seen.append(ev)
        await source.ack(ev)
        if len(seen) == 2:
            await source.aclose()
    assert [e.event_id for e in seen] == ["a", "b"] and await queue.depth() == 0


@pytest.mark.anyio
async def test_local_queue_source_leaves_unacked_events_in_the_queue():
    queue = MemoryLocalQueue()
    await queue.put(event("a"))
    source = LocalQueueSource(queue, poll_interval=0.001)
    with anyio.move_on_after(0.05):
        async for ev in source.events():
            await source.nack(ev, error=RuntimeError("boom"))
    assert await queue.depth() == 1
