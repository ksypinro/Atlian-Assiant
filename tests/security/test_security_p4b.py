"""P4b security control (design section 17): SEC-14 Slack / Teams / Twilio / IMAP credentials."""
from __future__ import annotations

import pytest

from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.model import ChangeAction
from atlian_assiant.events.provision.slack import BOT_SCOPES, PRIVATE_SCOPES, socket_mode_manifest
from atlian_assiant.events.provision.teams import GRAPH_PERMISSION_CANDIDATES, TeamsChannelProvisioner
from atlian_assiant.events.sources import ImapSource, SlackSocketModeSource, TwilioMessageLogSource
from atlian_assiant.events.stores import MemoryCheckpointStore
from tests.events.methods.humansim import FakeImap, FakeSocket, mail, slack_envelope, twilio_http

pytestmark = [pytest.mark.layer_11, pytest.mark.anyio]


def test_sec_14_credentials_are_secrets_never_shown_in_repr_or_describe():
    cfg = EventPlaneConfig(slack_app_token="xapp-SECRET", slack_bot_token="xoxb-SECRET", imap_password="pw-SECRET", twilio_auth_token="tw-SECRET", msgraph_client_secret="gr-SECRET")
    shown = repr(cfg) + str(cfg.model_dump())
    for secret in ("xapp-SECRET", "xoxb-SECRET", "pw-SECRET", "tw-SECRET", "gr-SECRET"):
        assert secret not in shown
    assert cfg.slack_app_token.get_secret_value() == "xapp-SECRET"


def test_sec_14_slack_bot_scopes_are_least_privilege_read_only_and_socket_mode_needs_no_public_url():
    m = socket_mode_manifest()
    scopes = m["oauth_config"]["scopes"]["bot"]
    assert scopes == list(BOT_SCOPES) and not any(s.split(":")[0] in ("chat", "files", "users", "admin") or s.endswith(":write") for s in scopes)
    assert "groups:history" not in scopes and set(PRIVATE_SCOPES) <= set(socket_mode_manifest(private_channels=True)["oauth_config"]["scopes"]["bot"])   # private channels are opt-in
    assert m["settings"]["socket_mode_enabled"] is True and "request_url" not in str(m)


@pytest.mark.anyio
async def test_sec_14_slack_source_honours_the_channel_allow_list_and_never_reads_other_channels():
    sock = FakeSocket([slack_envelope(channel="C-ALLOWED", text="PROJ-1", ts="1.1"), slack_envelope(channel="C-OTHER", text="PROJ-2", ts="2.2", envelope_id="e2")])
    src = SlackSocketModeSource(sock, channels=["C-ALLOWED"], project_keys=["PROJ"])
    assert [e.entity_key async for e in src.events()] == ["PROJ-1"] and src.ignored["channel"] == 1


def test_sec_14_graph_permission_is_resource_specific_to_the_configured_team_and_channel():
    assert "RSC" in GRAPH_PERMISSION_CANDIDATES[0] and "tenant-wide" in GRAPH_PERMISSION_CANDIDATES[1] and "avoid" in GRAPH_PERMISSION_CANDIDATES[1]
    plan_text = TeamsChannelProvisioner(team_id="T9", channel_id="C9").guided_steps()[2]
    assert "team T9 / channel C9" in plan_text and "ChannelMessage.Read.Group" in plan_text


@pytest.mark.anyio
async def test_sec_14_imap_is_read_only_with_a_sender_allow_list_and_optional_authentication_check():
    imap = FakeImap([mail("x", "PROJ-1", sender="evil@attacker.example", message_id="<e>"), mail("y", "PROJ-2", message_id="<g>", auth="mx; dkim=pass")])
    src = ImapSource(imap, MemoryCheckpointStore(), allowed_senders=["@example.atlassian.net"], project_keys=["PROJ"], require_authenticated=True)
    events = await src.poll_once()
    assert [e.event_id for e in events] == ["<g>"] and imap.selected == [("INBOX", True)] and not imap.stored                 # READ-ONLY select, no STORE/flags
    assert src.ignored["sender"] == 1 and all(e.action is ChangeAction.NOTIFIED for e in events)


@pytest.mark.anyio
async def test_sec_14_twilio_credentials_go_in_a_header_never_in_the_url():
    http, state = twilio_http([])
    src = TwilioMessageLogSource(http, MemoryCheckpointStore(), account_sid="AC1", auth_token="TOKEN-SECRET", from_number="+1555")
    await src.poll_once()
    params, auth = state["requests"][0]
    assert "TOKEN-SECRET" not in str(params) and auth.startswith("Basic ")


def test_sec_14_rss_feed_urls_never_contain_credentials():
    from atlian_assiant.events.provision.rss import RssFeedProvisioner
    for p in (RssFeedProvisioner.jira_filter("example.atlassian.net", 1), RssFeedProvisioner.confluence("example.atlassian.net", spaces=["ENG"])):
        assert "@" not in p.feed.url.split("//", 1)[1].split("/")[0] and "token" not in p.feed.url.lower() and "password" not in p.feed.url.lower()
