"""Fakes for the human channels: IMAP mailbox, Slack Socket Mode, Microsoft Graph, Twilio, RSS feeds, JSM Operations."""
from __future__ import annotations

import json
from datetime import timedelta
from email.message import EmailMessage

import httpx

from atlian_assiant.events.verify import EventInbox

SITE = "https://example.atlassian.net"


def mail(subject: str, body: str, *, sender: str = "jira@example.atlassian.net", message_id: str = "<m1@x>", html: bool = False, auth: str | None = None) -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = f"Jira <{sender}>", "bot@corp.example", subject, message_id
    if auth:
        m["Authentication-Results"] = auth
    if html:
        m.set_content("see html")
        m.add_alternative(body, subtype="html")
    else:
        m.set_content(body)
    return m.as_bytes()


class FakeImap:
    """imaplib-shaped: select(readonly) / uid('search'|'fetch')."""

    def __init__(self, messages: list[bytes] | None = None) -> None:
        self.messages: dict[int, bytes] = {i + 1: m for i, m in enumerate(messages or [])}
        self.selected: list[tuple[str, bool]] = []
        self.stored = False

    def add(self, raw: bytes) -> int:
        uid = max(self.messages, default=0) + 1
        self.messages[uid] = raw
        return uid

    def select(self, folder: str, readonly: bool = False):
        self.selected.append((folder, readonly))
        return "OK", [b"1"]

    def uid(self, command: str, *args):
        if command == "search":
            start = int(args[1].split()[1].split(":")[0])
            uids = [u for u in self.messages if u >= start] or ([max(self.messages)] if self.messages else [])      # IMAP quirk: n:* includes the last message
            return "OK", [" ".join(map(str, uids)).encode()]
        if command == "fetch":
            return "OK", [(b"1 (RFC822 {n})", self.messages[int(args[0])]), b")"]
        if command == "store":
            self.stored = True
        return "OK", []


class FakeSocket:
    def __init__(self, envelopes):
        self.queue, self.acked = list(envelopes), []

    async def receive(self):
        return self.queue.pop(0) if self.queue else None

    async def acknowledge(self, envelope_id):
        self.acked.append(envelope_id)


def slack_envelope(channel="C1", ts="1.1", text="", attachments=None, bot_id=None, envelope_id="env1", subtype=None):
    ev = {"type": "message", "channel": channel, "ts": ts, "text": text, **({"attachments": attachments} if attachments else {}), **({"bot_id": bot_id} if bot_id else {}),
          **({"subtype": subtype} if subtype else {})}
    return {"envelope_id": envelope_id, "type": "events_api", "payload": {"event": ev}}


def graph_http(pages):
    """pages: list of response bodies served in order; records requests."""
    state = {"requests": [], "pages": list(pages)}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append((str(request.url), request.headers.get("authorization")))
        return httpx.Response(200, json=state["pages"].pop(0) if state["pages"] else {"value": [], "@odata.deltaLink": "https://graph/delta?token=end"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), state


def twilio_http(messages):
    state = {"requests": [], "messages": list(messages)}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append((dict(request.url.params), request.headers.get("authorization")))
        return httpx.Response(200, json={"messages": state["messages"]})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), state


RSS_XML = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>[PROJ-1] Login fails</title><link>https://example.atlassian.net/browse/PROJ-1</link><guid>g1</guid><description>d</description><pubDate>Mon, 01 Sep 2026 10:00:00 GMT</pubDate></item>
<item><title>[PROJ-2] Slow page</title><link>https://example.atlassian.net/browse/PROJ-2</link><guid>g2</guid></item>
</channel></rss>"""
ATOM_XML = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>a1</id><title>Runbook updated</title><link href="https://example.atlassian.net/wiki/spaces/ENG/pages/4455/Runbook"/>
<updated>2026-09-01T10:00:00Z</updated><summary>edited</summary></entry></feed>"""


def rss_http(xml=RSS_XML, etag='"v1"'):
    state = {"requests": [], "xml": xml, "etag": etag}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(dict(request.headers))
        if request.headers.get("if-none-match") == state["etag"]:
            return httpx.Response(304)
        return httpx.Response(200, text=state["xml"], headers={"ETag": state["etag"], "Last-Modified": "Mon, 01 Sep 2026 10:00:00 GMT", "Content-Type": "application/rss+xml"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), state


def ops_http():
    state = {"alerts": [], "requests": [], "closed": [], "acked": [], "n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/v1", 1)[1]
        state["requests"].append((request.method, path, dict(request.url.params)))
        if request.method == "GET" and path == "/alerts":
            after = request.url.params.get("query", "").split("> ")[-1] if request.url.params.get("query") else None
            return httpx.Response(200, json={"values": [a for a in state["alerts"] if not after or a["updatedAt"] > after]})
        if request.method == "POST" and path == "/alerts":
            body = json.loads(request.content)
            state["n"] += 1
            alert = {"id": f"al{state['n']}", "message": body["message"], "alias": body.get("alias"), "status": "open", "createdAt": "2026-09-01T10:00:00Z",
                     "updatedAt": "2026-09-01T10:00:00Z", "priority": body.get("priority")}
            state["alerts"].append(alert)
            return httpx.Response(200, json=alert)
        if request.method == "POST" and path.endswith("/close"):
            aid = path.split("/")[2]
            state["closed"].append(aid)
            for a in state["alerts"]:
                if a["id"] == aid:
                    a.update(status="closed", updatedAt="2026-09-01T10:05:00Z")
            return httpx.Response(200, json={})
        if request.method == "POST" and path.endswith("/acknowledge"):
            state["acked"].append(path.split("/")[2])
            return httpx.Response(200, json={})
        if path.endswith("/on-calls"):
            return httpx.Response(200, json={"data": {"onCallParticipants": ["ann@example.com"]}})
        return httpx.Response(404, json={})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), state


async def push_all(inbox: EventInbox, events) -> None:
    for e in events:
        await inbox.push(e)


T = timedelta(seconds=2)
