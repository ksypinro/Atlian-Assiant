"""AtlassianLinkParser for email / Slack / Teams / SMS / RSS and digest splitting (design 14.4, 14.10)."""
from __future__ import annotations

import pytest

from atlian_assiant.capabilities import Product
from atlian_assiant.core.errors import InvalidRequestError
from atlian_assiant.events.human.parsers import html_to_text, parse_email, rss_entry_text, slack_text, teams_text
from atlian_assiant.events.model import ChangeAction, EntityType, EventSourceKind
from atlian_assiant.events.model import EntityType as E
from atlian_assiant.events.normalize import AtlassianLinkParser
from atlian_assiant.events.sources.rss import parse_feed

from ..events.methods.humansim import ATOM_XML, RSS_XML, SITE, mail

pytestmark = [pytest.mark.layer_11]
PARSER = AtlassianLinkParser(["PROJ", "OPS"])


def test_plain_email_is_parsed_with_sender_subject_message_id_and_product_hint():
    p = parse_email(mail("[JIRA] (PROJ-12) Login fails", f"Ann changed PROJ-12\n{SITE}/browse/PROJ-12", message_id="<abc@jira>"))
    assert (p.sender, p.message_id, p.subject) == ("jira@example.atlassian.net", "<abc@jira>", "[JIRA] (PROJ-12) Login fails") and p.product_hint == "jira" and "PROJ-12" in p.text
    assert parse_email(mail("Page updated", f"{SITE}/wiki/spaces/ENG/pages/4455/Runbook")).product_hint == "confluence"
    assert parse_email(mail("hello", "nothing here")).product_hint == "unknown"


def test_html_email_keeps_link_targets_and_drops_markup():
    html = f'<html><body><p>Ann updated <a href="{SITE}/browse/PROJ-7">PROJ-7</a></p><table><tr><td>x &amp; y</td></tr></table></body></html>'
    text = parse_email(mail("n", html, html=True)).text
    assert f"{SITE}/browse/PROJ-7" in text and "x & y" in text and "<" not in text
    assert html_to_text("<br>a<br>b") == "a\nb"


def test_emails_with_odd_or_missing_parts_never_raise():
    p = parse_email(b"From: a@b.c\nSubject: x\n\n")
    assert p.sender == "a@b.c" and p.message_id.startswith("nomid-") and p.text == ""
    assert parse_email(b"garbage").subject == ""


def test_a_digest_is_split_into_one_event_per_referenced_entity():
    digest = f"Daily digest\nPROJ-1 {SITE}/browse/PROJ-2 {SITE}/wiki/spaces/ENG/pages/9/Page OPS-3"
    events = PARSER.events(digest, source=EventSourceKind.EMAIL_IMAP, native_type="email:jira", message_id="<d1>", method_id="DM-29")
    assert sorted(e.entity_key or e.entity_id for e in events) == ["9", "OPS-3", "PROJ-1", "PROJ-2"]
    assert {e.event_id for e in events} == {"<d1>#PROJ-1", "<d1>#PROJ-2", "<d1>#9", "<d1>#OPS-3"} and all(e.action is ChangeAction.NOTIFIED for e in events)
    assert {e.product for e in events} == {Product.JIRA, Product.CONFLUENCE}


def test_slack_attachments_blocks_and_text_are_flattened():
    event = {"text": "", "attachments": [{"title": "PROJ-5 Login fails", "title_link": f"{SITE}/browse/PROJ-5", "text": "status changed"}],
             "blocks": [{"text": {"text": "block text"}, "elements": [{"url": f"{SITE}/browse/PROJ-6"}]}]}
    text = slack_text(event)
    assert "PROJ-5 Login fails" in text and f"{SITE}/browse/PROJ-5" in text and "block text" in text and f"{SITE}/browse/PROJ-6" in text
    assert [r.key for r in PARSER.parse(text)] == ["PROJ-5", "PROJ-6"]


def test_teams_html_and_plain_bodies_and_sms_and_rss_entries():
    assert f"{SITE}/browse/PROJ-8" in teams_text({"body": {"contentType": "html", "content": f'<a href="{SITE}/browse/PROJ-8">x</a>'}})
    assert teams_text({"body": {"contentType": "text", "content": "PROJ-9 done"}}) == "PROJ-9 done"
    sms = PARSER.events("[Jira] PROJ-4 assigned to you", source=EventSourceKind.TWILIO_LOG, native_type="sms:twilio", message_id="SM1", method_id="DM-23")
    assert [e.entity_key for e in sms] == ["PROJ-4"] and sms[0].event_id == "SM1"
    entries = parse_feed(RSS_XML) + parse_feed(ATOM_XML)
    assert [e["guid"] for e in entries] == ["g1", "g2", "a1"] and entries[2]["link"].endswith("/Runbook")
    assert "PROJ-1" in rss_entry_text(entries[0])
    with pytest.raises(InvalidRequestError, match="not valid XML"):
        parse_feed("<html><body>login</body>")


def test_messages_with_no_resolvable_reference_are_still_emitted_as_unknown():
    events = PARSER.events("hello", source=EventSourceKind.SLACK_SOCKET_MODE, native_type="slack:message", message_id="C1:1.2", method_id="DM-21")
    assert len(events) == 1 and events[0].entity_type is E.UNKNOWN and events[0].event_id == "C1:1.2"
    assert EntityType.ALERT.value == "alert"
