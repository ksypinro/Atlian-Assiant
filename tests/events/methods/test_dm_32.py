"""DM-32: Jira Cloud for Slack (SlackChannelProvisioner(product="jira"), SlackSocketModeSource, verify_dm_32)."""
from __future__ import annotations

import json

import pytest

from atlian_assiant.core.errors import InvalidRequestError, PolicyDeniedError
from atlian_assiant.events.catalog import method
from atlian_assiant.events.provision.base import EventProvisioner, ProvisionMode
from atlian_assiant.events.provision.slack import (
    BOT_SCOPES,
    PRIVATE_SCOPES,
    SlackChannelProvisioner,
    socket_mode_manifest,
)
from atlian_assiant.events.verify import verify_dm_32
from atlian_assiant.policy import CallbackConfirmation, DenyAll

from .slackcases import check_consumer, check_verify

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)


class FakeSlack:
    def __init__(self, existing=()):
        self.channels = [{"id": f"C{i}", "name": n} for i, n in enumerate(existing)]
        self.invited, self.archived, self.created = [], [], []

    async def conversations_list(self, types):
        return {"channels": self.channels}

    async def conversations_create(self, name, is_private):
        self.created.append((name, is_private))
        ch = {"id": f"CNEW{len(self.created)}", "name": name}
        self.channels.append(ch)
        return {"channel": ch}

    async def conversations_invite(self, channel, users):
        self.invited.append((channel, users))

    async def conversations_archive(self, channel):
        self.archived.append(channel)


def test_dm_32_catalog_entry():
    m = method("DM-32")
    assert m.consumer.endswith("SlackSocketModeSource") and isinstance(SlackChannelProvisioner(product="jira"), EventProvisioner)


def test_dm_32_socket_mode_manifest_is_read_only_outbound_and_private_scopes_are_opt_in():
    m = socket_mode_manifest()
    assert m["settings"]["socket_mode_enabled"] is True and m["oauth_config"]["scopes"]["bot"] == list(BOT_SCOPES) and "request_url" not in json.dumps(m)
    assert m["settings"]["event_subscriptions"]["bot_events"] == ["message.channels"]
    priv = socket_mode_manifest(private_channels=True)
    assert set(PRIVATE_SCOPES) <= set(priv["oauth_config"]["scopes"]["bot"]) and "message.groups" in priv["settings"]["event_subscriptions"]["bot_events"]
    assert not any("write" in s or "post" in s for s in m["oauth_config"]["scopes"]["bot"])


async def test_dm_32_provision_guided_walks_through_the_app_install_subscriptions_and_manifest():
    prov = SlackChannelProvisioner(product="jira", subscriptions=["project = PROJ"])
    plan = await prov.plan()
    assert plan.method_id == "DM-32" and plan.outputs["mode"] == "guided" and any("Socket Mode" in w for w in plan.warnings)
    out = await prov.apply(plan, confirm=APPROVE)
    text = out["instructions"]
    assert "Install the Jira Cloud Slack app" in text and "/jira connect" in text and "project = PROJ" in text and "socket_mode_enabled" in text and "connections:write" in text


async def test_dm_32_provision_api_creates_the_channel_invites_the_bot_and_is_idempotent():
    slack = FakeSlack()
    prov = SlackChannelProvisioner(product="jira", channel="#atlassian-events", slack=slack, bot_user_id="U-BOT")
    plan = await prov.plan()
    assert plan.outputs["mode"] == "api"
    out = await prov.apply(plan, confirm=APPROVE)
    assert slack.created == [("atlassian-events", False)] and slack.invited == [("CNEW1", "U-BOT")] and out["created"] == "true"
    again = SlackChannelProvisioner(product="jira", channel="atlassian-events", slack=slack, bot_user_id="U-BOT")
    out2 = await again.apply(await again.plan(), confirm=APPROVE)
    assert out2["created"] == "false" and len(slack.created) == 1
    await again.teardown(confirm=APPROVE)
    assert slack.archived == [out2["channel_id"]]


async def test_dm_32_provision_declined_creates_no_channel_and_unknown_products_are_rejected():
    slack = FakeSlack()
    prov = SlackChannelProvisioner(product="jira", slack=slack)
    with pytest.raises(Exception, match="declined"):
        await prov.apply(await prov.plan(), confirm=DenyAll())
    assert slack.created == []
    with pytest.raises(InvalidRequestError):
        SlackChannelProvisioner(product="bitbucket")
    with pytest.raises(PolicyDeniedError):
        await prov.plan(mode=ProvisionMode.BROWSER)


async def test_dm_32_consumer_and_verify():
    await check_consumer("DM-32")
    await check_verify(verify_dm_32, "DM-32")
