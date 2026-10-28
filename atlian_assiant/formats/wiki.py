"""Jira/Confluence wiki markup <-> Markdown (Data Center rich text; design 10.2).

Covers headings, bold/italic/monospace/strike, lists, code blocks, quotes, links and rules. Anything else (macros) passes through unchanged.
"""
from __future__ import annotations

import re

_H = re.compile(r"^h([1-6])\.\s+(.*)$")
_LINK = re.compile(r"\[([^\]|]+)\|([^\]]+)\]")
_BARE = re.compile(r"\[(https?://[^\]|]+)\]")
_BOLD = re.compile(r"(?<![\w*])\*(\S(?:[^*\n]*\S)?)\*(?![\w*])")
_ITALIC = re.compile(r"(?<![\w_])_(\S(?:[^_\n]*\S)?)_(?![\w_])")
_MONO = re.compile(r"\{\{([^}]+)\}\}")
_STRIKE = re.compile(r"(?<![\w-])-(\S(?:[^-\n]*\S)?)-(?![\w-])")
_MD_H = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_LIST = re.compile(r"^(\s*)([-*]|\d+\.)\s+(.*)$")


def _inline(s: str) -> str:
    s = _LINK.sub(lambda m: f"[{m.group(1)}]({m.group(2)})", s)
    s = _BARE.sub(lambda m: f"<{m.group(1)}>", s)
    s = _MONO.sub(lambda m: f"`{m.group(1)}`", s)
    s = _BOLD.sub(lambda m: f"**{m.group(1)}**", s)
    s = _ITALIC.sub(lambda m: f"*{m.group(1)}*", s)
    return _STRIKE.sub(lambda m: f"~~{m.group(1)}~~", s)


def wiki_to_markdown(text: str | None) -> str:
    """Wiki markup -> Markdown. ``None`` / empty gives an empty string."""
    if not text:
        return ""
    out: list[str] = []
    in_code = False
    for raw in text.replace("\r\n", "\n").split("\n"):
        stripped = raw.strip()
        if stripped.startswith("{code"):
            if in_code:
                out.append("```")
            else:
                lang = re.match(r"\{code(?::([\w+-]+))?\}", stripped)
                out.append("```" + (lang.group(1) if lang and lang.group(1) else ""))
            in_code = not in_code
            continue
        if in_code:
            out.append(raw)
            continue
        if stripped == "----":
            out.append("---")
            continue
        h = _H.match(raw)
        if h:
            out.append("#" * int(h.group(1)) + " " + _inline(h.group(2)))
            continue
        bq = re.match(r"^bq\.\s+(.*)$", raw)
        if bq:
            out.append("> " + _inline(bq.group(1)))
            continue
        m = re.match(r"^([*#]+)\s+(.*)$", raw)
        if m:
            marker = "1." if m.group(1)[-1] == "#" else "-"
            out.append("  " * (len(m.group(1)) - 1) + marker + " " + _inline(m.group(2)))
            continue
        out.append(_inline(raw))
    return "\n".join(out)


def _md_inline(s: str) -> str:
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", lambda m: f"[{m.group(1)}|{m.group(2)}]", s)
    s = re.sub(r"\*\*(.+?)\*\*", lambda m: f"\x00{m.group(1)}\x00", s)             # protect bold while italics are converted
    s = re.sub(r"(?<![\w*])\*(?!\*)(.+?)(?<!\*)\*(?![\w*])", lambda m: f"_{m.group(1)}_", s)
    s = s.replace("\x00", "*")
    s = re.sub(r"`([^`]+)`", lambda m: "{{" + m.group(1) + "}}", s)
    return re.sub(r"~~(.+?)~~", lambda m: f"-{m.group(1)}-", s)


def markdown_to_wiki(md: str | None) -> str:
    """Markdown -> wiki markup (what Data Center REST expects for descriptions and comments)."""
    if not md:
        return ""
    out: list[str] = []
    in_code = False
    for raw in md.replace("\r\n", "\n").split("\n"):
        if raw.strip().startswith("```"):
            if in_code:
                out.append("{code}")
            else:
                lang = raw.strip()[3:].strip()
                out.append("{code" + (f":{lang}" if lang else "") + "}")
            in_code = not in_code
            continue
        if in_code:
            out.append(raw)
            continue
        if raw.strip() == "---":
            out.append("----")
            continue
        h = _MD_H.match(raw)
        if h:
            out.append(f"h{len(h.group(1))}. {_md_inline(h.group(2))}")
            continue
        if raw.startswith("> "):
            out.append("bq. " + _md_inline(raw[2:]))
            continue
        m = _MD_LIST.match(raw)
        if m:
            depth = len(m.group(1)) // 2 + 1
            out.append(("#" if m.group(2)[0].isdigit() else "*") * depth + " " + _md_inline(m.group(3)))
            continue
        out.append(_md_inline(raw))
    return "\n".join(out)
