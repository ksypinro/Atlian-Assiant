"""Atlassian Document Format <-> Markdown <-> plain text (design section 7.7).

Unknown nodes are preserved as fenced ``adf-node`` JSON blocks so a round trip never loses information.
Mentions render as ``@[Name](accountId)``.
"""
from __future__ import annotations

import json
import re
from typing import Any

Node = dict[str, Any]


def _marks_wrap(text: str, marks: list[Node]) -> str:
    for m in marks:
        t = m.get("type")
        if t == "code":
            text = f"`{text}`"
        elif t == "strong":
            text = f"**{text}**"
        elif t == "em":
            text = f"*{text}*"
        elif t == "strike":
            text = f"~~{text}~~"
        elif t == "link":
            text = f"[{text}]({m.get('attrs', {}).get('href', '')})"
    return text


def _inline(nodes: list[Node]) -> str:
    out: list[str] = []
    for n in nodes:
        t = n.get("type")
        if t == "text":
            out.append(_marks_wrap(n.get("text", ""), n.get("marks", [])))
        elif t == "hardBreak":
            out.append("  \n")
        elif t == "mention":
            a = n.get("attrs", {})
            out.append(f"@[{str(a.get('text', '')).lstrip('@')}]({a.get('id', '')})")
        elif t == "emoji":
            out.append(n.get("attrs", {}).get("shortName", ""))
        elif t in ("inlineCard", "blockCard"):
            url = n.get("attrs", {}).get("url", "")
            out.append(f"<{url}>")
        else:
            out.append(_unknown(n))
    return "".join(out)


def _unknown(n: Node) -> str:
    return "\n```adf-node\n" + json.dumps(n, separators=(",", ":"), ensure_ascii=False) + "\n```\n"


def _list(node: Node, depth: int = 0) -> str:
    ordered = node["type"] == "orderedList"
    lines: list[str] = []
    for i, item in enumerate(node.get("content", []), start=int(node.get("attrs", {}).get("order", 1))):
        bullet = f"{i}." if ordered else "-"
        text = "\n".join(p for p in (_block(c) for c in item.get("content", [])) if p)
        first, *rest = text.split("\n") or [""]
        lines.append(f"{bullet} {first}")
        lines += [("  " + r) if r.strip() else r for r in rest]
    return "\n".join(lines)


def _table(node: Node) -> str:
    rows = [[_inline(p.get("content", [])) if (p := (c.get("content") or [{}])[0]) else "" for c in r.get("content", [])]
            for r in node.get("content", [])]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _block(n: Node, depth: int = 0) -> str:
    t = n.get("type")
    c = n.get("content", [])
    if t == "paragraph":
        return _inline(c)
    if t == "heading":
        return "#" * int(n.get("attrs", {}).get("level", 1)) + " " + _inline(c)
    if t in ("bulletList", "orderedList"):
        return _list(n)
    if t == "codeBlock":
        lang = n.get("attrs", {}).get("language", "") or ""
        return f"```{lang}\n" + "".join(x.get("text", "") for x in c) + "\n```"
    if t == "blockquote":
        return "\n".join("> " + line for line in "\n\n".join(_block(x) for x in c).split("\n"))
    if t == "rule":
        return "---"
    if t == "table":
        return _table(n)
    if t == "panel":
        return "> **" + str(n.get("attrs", {}).get("panelType", "note")).title() + ":** " + "\n> ".join(_block(x) for x in c)
    return _unknown(n).strip("\n")


def adf_to_markdown(doc: Node | str | None) -> str:
    if doc is None:
        return ""
    if isinstance(doc, str):
        return doc
    return "\n\n".join(b for b in (_block(n) for n in doc.get("content", [])) if b != "").strip()


def adf_to_text(doc: Node | str | None) -> str:
    """Plain text: formatting dropped, mentions as ``@name``."""
    md = adf_to_markdown(doc)
    md = re.sub(r"@\[([^\]]*)\]\([^)]*\)", r"@\1", md)
    md = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)
    md = re.sub(r"(\*\*|~~|`|\*)", "", md)
    return re.sub(r"^#{1,6} ", "", md, flags=re.MULTILINE)


# ---------------------------------------------------------------- markdown -> ADF
_INLINE = re.compile(
    r"(?P<mention>@\[(?P<mname>[^\]]*)\]\((?P<mid>[^)]*)\))"
    r"|(?P<link>\[(?P<ltext>[^\]]+)\]\((?P<lurl>[^)]+)\))"
    r"|(?P<code>`(?P<ctext>[^`]+)`)"
    r"|(?P<strong>\*\*(?P<stext>.+?)\*\*)"
    r"|(?P<strike>~~(?P<xtext>.+?)~~)"
    r"|(?P<em>\*(?P<etext>[^*]+)\*)"
    r"|(?P<card><(?P<curl>https?://[^>]+)>)"
)


def _text(text: str, *marks: Node) -> Node:
    n: Node = {"type": "text", "text": text}
    if marks:
        n["marks"] = list(marks)
    return n


def _parse_inline(s: str, marks: tuple[Node, ...] = ()) -> list[Node]:
    out: list[Node] = []
    pos = 0
    for m in _INLINE.finditer(s):
        if m.start() > pos:
            out.append(_text(s[pos:m.start()], *marks))
        if m.group("mention"):
            out.append({"type": "mention", "attrs": {"id": m.group("mid"), "text": "@" + m.group("mname")}})
        elif m.group("link"):
            out += _parse_inline(m.group("ltext"), marks + ({"type": "link", "attrs": {"href": m.group("lurl")}},))
        elif m.group("code"):
            out.append(_text(m.group("ctext"), *marks, {"type": "code"}))
        elif m.group("strong"):
            out += _parse_inline(m.group("stext"), marks + ({"type": "strong"},))
        elif m.group("strike"):
            out += _parse_inline(m.group("xtext"), marks + ({"type": "strike"},))
        elif m.group("em"):
            out += _parse_inline(m.group("etext"), marks + ({"type": "em"},))
        elif m.group("card"):
            out.append({"type": "inlineCard", "attrs": {"url": m.group("curl")}})
        pos = m.end()
    if pos < len(s):
        out.append(_text(s[pos:], *marks))
    return [n for n in out if n.get("text", "x") != ""]


def _para(lines: list[str]) -> Node:
    content: list[Node] = []
    for i, line in enumerate(lines):
        stripped = line.rstrip()
        content += _parse_inline(stripped.strip())
        if i < len(lines) - 1:
            content.append({"type": "hardBreak"})
    return {"type": "paragraph", "content": content}


def _parse_list(lines: list[str], i: int, indent: int) -> tuple[Node, int]:
    first = lines[i]
    ordered = bool(re.match(r"\s*\d+\.\s", first))
    items: list[Node] = []
    pat = re.compile(r"^(\s*)(?:(\d+)\.|[-*+])\s+(.*)$")
    while i < len(lines):
        m = pat.match(lines[i])
        if not m or len(m.group(1)) != indent or bool(m.group(2)) != ordered:
            break
        item_content: list[Node] = [_para([m.group(3)])]
        i += 1
        while i < len(lines):
            nm = pat.match(lines[i])
            if nm and len(nm.group(1)) > indent:
                sub, i = _parse_list(lines, i, len(nm.group(1)))
                item_content.append(sub)
            else:
                break
        items.append({"type": "listItem", "content": item_content})
    return {"type": "orderedList" if ordered else "bulletList", "content": items}, i


def markdown_to_adf(md: str) -> Node:
    lines = md.replace("\r\n", "\n").split("\n")
    blocks: list[Node] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif line.startswith("```"):
            lang = line[3:].strip()
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            if lang == "adf-node":
                blocks.append(json.loads("\n".join(body)))
            else:
                node: Node = {"type": "codeBlock", "content": [{"type": "text", "text": "\n".join(body)}]}
                if lang:
                    node["attrs"] = {"language": lang}
                blocks.append(node)
        elif re.match(r"^#{1,6} ", line):
            level = len(line) - len(line.lstrip("#"))
            blocks.append({"type": "heading", "attrs": {"level": level}, "content": _parse_inline(line[level:].strip())})
            i += 1
        elif re.match(r"^(---|\*\*\*|___)\s*$", line):
            blocks.append({"type": "rule"})
            i += 1
        elif line.startswith(">"):
            quote: list[str] = []
            while i < len(lines) and lines[i].startswith(">"):
                quote.append(lines[i][1:].lstrip())
                i += 1
            blocks.append({"type": "blockquote", "content": [_para(quote)]})
        elif re.match(r"^\s*(\d+\.|[-*+])\s+", line):
            indent = len(line) - len(line.lstrip())
            node2, i = _parse_list(lines, i, indent)
            blocks.append(node2)
        elif line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|?\s*$", lines[i + 1]):
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].startswith("|"):
                if not re.match(r"^\|[\s:|-]+\|?\s*$", lines[i]):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            trs = [{"type": "tableRow", "content": [
                {"type": "tableHeader" if r == 0 else "tableCell", "attrs": {},
                 "content": [{"type": "paragraph", "content": _parse_inline(c)}]} for c in row]}
                for r, row in enumerate(rows)]
            blocks.append({"type": "table", "attrs": {"isNumberColumnEnabled": False, "layout": "default"}, "content": trs})
        else:
            para: list[str] = []
            while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,6} |```|>|\s*(\d+\.|[-*+])\s+|---\s*$)", lines[i]):
                para.append(lines[i])
                i += 1
            blocks.append(_para(para))
    return {"version": 1, "type": "doc", "content": blocks}
