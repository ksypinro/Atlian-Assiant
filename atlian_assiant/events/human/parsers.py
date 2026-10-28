"""Channel parsers (design 14.4 'Human-channel parsing'): email, Slack, Teams, SMS and RSS entries -> plain text for the AtlassianLinkParser."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default
from email.utils import parseaddr
from html import unescape
from typing import Any

_TAG = re.compile(r"<[^>]+>")
_ANCHOR = re.compile(r'<a\s[^>]*?href=["\']([^"\']+)["\'][^>]*>', re.I)
_WS = re.compile(r"[ \t\r\f\v]+")


def html_to_text(html: str) -> str:
    """Tags removed, but link targets kept (they carry the issue/page URLs the link parser needs)."""
    with_links = _ANCHOR.sub(lambda m: f" {m.group(1)} ", html)
    text = unescape(_TAG.sub(" ", with_links.replace("<br>", "\n").replace("</p>", "\n").replace("</tr>", "\n")))
    return "\n".join(_WS.sub(" ", line).strip() for line in text.splitlines() if line.strip())


@dataclass(frozen=True)
class ParsedEmail:
    message_id: str
    sender: str                 # bare address, lower-case
    subject: str
    text: str
    headers: Mapping[str, str]

    @property
    def product_hint(self) -> str:
        blob = f"{self.subject} {self.text}"
        if "/wiki/" in blob:
            return "confluence"
        if "/browse/" in blob or re.search(r"\b[A-Z][A-Z0-9]+-\d+\b", blob):
            return "jira"
        return "unknown"


def parse_email(raw: bytes) -> ParsedEmail:
    """RFC 822 bytes -> ``ParsedEmail``. Prefers text/plain; falls back to HTML with links preserved. Never raises on odd MIME."""
    msg = message_from_bytes(raw, policy=default)
    chunks: list[str] = []
    for part in msg.walk():                               # plain AND html: Atlassian mail often keeps the useful links only in the HTML part
        if part.get_content_maintype() != "text" or part.get_content_disposition() == "attachment" or not isinstance(part, EmailMessage):
            continue
        try:
            content = str(part.get_content())
        except (LookupError, UnicodeDecodeError):
            continue
        chunks.append(html_to_text(content) if part.get_content_type() == "text/html" else content)
    text = "\n".join(c for c in chunks if c.strip())
    return ParsedEmail(message_id=(msg["Message-ID"] or "").strip() or f"nomid-{abs(hash(raw)) % 10**12}", sender=parseaddr(str(msg["From"] or ""))[1].lower(),
                       subject=str(msg["Subject"] or ""), text=text, headers={k: str(v) for k, v in msg.items()})


def slack_text(event: Mapping[str, Any]) -> str:
    """The message text plus attachment titles/links/text and block text (Jira Cloud for Slack posts attachments, not plain text)."""
    parts = [str(event.get("text") or "")]
    for att in event.get("attachments") or []:
        parts += [str(att.get(k) or "") for k in ("title", "title_link", "text", "fallback", "pretext")]
    for block in event.get("blocks") or []:
        t = block.get("text")
        if isinstance(t, Mapping):
            parts.append(str(t.get("text") or ""))
        for el in block.get("elements") or []:
            if isinstance(el, Mapping):
                parts.append(str(el.get("text") or el.get("url") or ""))
    return "\n".join(p for p in parts if p)


def teams_text(message: Mapping[str, Any]) -> str:
    body = message.get("body") or {}
    content = str(body.get("content") or "")
    return html_to_text(content) if body.get("contentType") == "html" else content


def rss_entry_text(entry: Mapping[str, Any]) -> str:
    return "\n".join(str(entry.get(k) or "") for k in ("title", "link", "summary"))
