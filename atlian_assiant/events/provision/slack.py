"""SlackChannelProvisioner (DM-32 Jira, DM-33 Confluence): guided Atlassian-app install + channel subscriptions; Slack Web API creates the channel and invites the bot."""
from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, ClassVar

from ...core.errors import InvalidRequestError
from .base import ProvisionMode
from .human import HumanProvisioner

BOT_SCOPES = ("channels:history", "channels:read")                      # read-only: public channels; add groups:history/groups:read ONLY for private channels
PRIVATE_SCOPES = ("groups:history", "groups:read")


def socket_mode_manifest(app_name: str = "atlassian-events", *, private_channels: bool = False) -> dict[str, Any]:
    """Slack app manifest for a Socket Mode, read-only listener (outbound WebSocket: no request URL, no public ingress)."""
    scopes = [*BOT_SCOPES, *(PRIVATE_SCOPES if private_channels else ())]
    return {"display_information": {"name": app_name}, "features": {"bot_user": {"display_name": app_name, "always_online": False}},
            "oauth_config": {"scopes": {"bot": scopes}},
            "settings": {"event_subscriptions": {"bot_events": ["message.channels", *(["message.groups"] if private_channels else [])]},
                         "socket_mode_enabled": True, "org_deploy_enabled": False, "token_rotation_enabled": False}}


class SlackChannelProvisioner(HumanProvisioner):
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-32", "DM-33"})
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.GUIDED, ProvisionMode.API})

    def __init__(self, *, product: str, channel: str = "atlassian-events", slack: Any = None, bot_user_id: str | None = None, private: bool = False,
                 subscriptions: Sequence[str] = (), probe: Any = None) -> None:
        super().__init__(probe=probe)
        if product not in ("jira", "confluence"):
            raise InvalidRequestError(f"unknown product {product!r}", hint="Use jira or confluence.")
        self.product, self.channel, self.slack, self.bot, self.private, self.subs = product, channel.lstrip("#"), slack, bot_user_id, private, list(subscriptions)
        self.channel_id: str | None = None

    @property
    def method_id(self) -> str:
        return "DM-32" if self.product == "jira" else "DM-33"

    def supported(self) -> frozenset[ProvisionMode]:
        return frozenset({ProvisionMode.GUIDED} | ({ProvisionMode.API} if self.slack is not None else set()))

    def guided_steps(self) -> list[str]:
        app = "Jira Cloud" if self.product == "jira" else "Confluence Cloud"
        what = "/jira connect, then /jira subscribe in the channel (filters / projects)" if self.product == "jira" else "/confluence subscribe: pick the spaces/pages"
        return [f"Install the {app} Slack app in the workspace (admin approval may be needed)", f"Create channel #{self.channel} and add the {app} app to it",
                f"In the channel run {what}" + (f": {', '.join(self.subs)}" if self.subs else ""),
                "Create the listener app from this manifest (api.slack.com/apps > From manifest):\n" + json.dumps(socket_mode_manifest(private_channels=self.private), indent=2),
                "Generate an app-level token with connections:write (xapp-...) and store it as a SECRET", "Invite the listener bot to the channel"]

    def api_steps(self) -> list[str]:
        return [f"Create channel #{self.channel}" + (" (private)" if self.private else ""), "Invite the listener bot", "The Atlassian app install and its subscriptions stay guided (admin consent)"]

    def warnings(self) -> tuple[str, ...]:
        return ("the listener only needs read scopes: " + ", ".join(BOT_SCOPES + (PRIVATE_SCOPES if self.private else ())),
                "Socket Mode is an outbound WebSocket: no public URL", "restrict the source with a channel allow-list; personal DMs only if the user opts in")

    def outputs(self) -> dict[str, str]:
        return {"channel": self.channel}

    async def _apply_api(self) -> dict[str, str]:
        existing = next((c for c in (await self.slack.conversations_list(types="private_channel" if self.private else "public_channel")).get("channels", [])
                         if c.get("name") == self.channel), None)
        if existing:
            self.channel_id = existing["id"]
        else:
            self.channel_id = (await self.slack.conversations_create(name=self.channel, is_private=self.private))["channel"]["id"]
        if self.bot:
            await self.slack.conversations_invite(channel=self.channel_id, users=self.bot)
        return {"channel_id": str(self.channel_id), "created": str(existing is None).lower()}

    async def _teardown_api(self) -> None:
        if self.channel_id and self.slack is not None:
            await self.slack.conversations_archive(channel=self.channel_id)
