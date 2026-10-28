"""Sinks that re-notify people from processed events: Slack, Teams, SMTP email, Twilio SMS, JSM Operations alerts. Messages carry only key, title and link, never bodies."""
from __future__ import annotations

from collections.abc import Callable
from email.message import EmailMessage
from typing import Any

from ..model import ChangeEvent


def summary(event: ChangeEvent, site: str | None = None) -> str:
    ref = event.entity_key or event.entity_id or event.event_id
    title = getattr(event.canonical, "summary", None) or getattr(event.canonical, "title", None) or ""
    link = f"https://{site}/browse/{event.entity_key}" if site and event.entity_key and event.product.value.startswith("jira") else ""
    return " ".join(p for p in (f"[{event.native_type}]", str(ref), str(title), link) if p)


class SlackSink:
    """``slack`` needs ``async chat_postMessage(channel, text)``."""

    def __init__(self, slack: Any, channel: str, *, site: str | None = None) -> None:
        self.slack, self.channel, self.site = slack, channel, site

    async def publish(self, event: ChangeEvent) -> None:
        await self.slack.chat_postMessage(channel=self.channel, text=summary(event, self.site))


class TeamsSink:
    """Posts to a Teams incoming-webhook / Workflows URL (``http`` is an httpx.AsyncClient)."""

    def __init__(self, http: Any, webhook_url: str, *, site: str | None = None) -> None:
        self.http, self.url, self.site = http, webhook_url, site

    async def publish(self, event: ChangeEvent) -> None:
        (await self.http.post(self.url, json={"text": summary(event, self.site)})).raise_for_status()


class EmailSink:
    """SMTP via an injected ``send(message)`` callable (``smtplib`` or ``aiosmtplib`` wrapper)."""

    def __init__(self, send: Callable[[EmailMessage], Any], sender: str, recipients: list[str], *, site: str | None = None) -> None:
        self._send, self.sender, self.recipients, self.site = send, sender, recipients, site

    async def publish(self, event: ChangeEvent) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, ", ".join(self.recipients), summary(event, self.site)[:200]
        msg.set_content(summary(event, self.site))
        result = self._send(msg)
        if hasattr(result, "__await__"):
            await result


class TwilioSmsSink:
    def __init__(self, http: Any, account_sid: str, auth_token: str, from_number: str, to_number: str, *, site: str | None = None) -> None:
        self.http, self.sid, self._auth, self.from_number, self.to, self.site = http, account_sid, (account_sid, auth_token), from_number, to_number, site

    async def publish(self, event: ChangeEvent) -> None:
        resp = await self.http.post(f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json", auth=self._auth,
                                    data={"From": self.from_number, "To": self.to, "Body": summary(event, self.site)[:300]})
        resp.raise_for_status()


class JsmOpsAlertSink:
    """Raises a JSM Operations alert (alias = the event's dedup key, so repeats collapse). ``ops`` is a ``JsmOpsClient``."""

    def __init__(self, ops: Any, *, priority: str = "P3", site: str | None = None) -> None:
        self.ops, self.priority, self.site = ops, priority, site

    async def publish(self, event: ChangeEvent) -> None:
        await self.ops.create_alert(summary(event, self.site)[:250], alias=event.dedup_key, priority=self.priority)
