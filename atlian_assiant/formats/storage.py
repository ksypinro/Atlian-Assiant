"""Confluence storage format (XHTML) <-> Markdown (design section 7.7).

Macros and other Confluence-specific elements (``ac:*`` / ``ri:*``) are kept verbatim inside placeholder comments so they
round-trip unchanged::

    <!-- confluence-macro:<name>:<base64 of the original XML> -->
"""
from __future__ import annotations

import base64
import html
import re
from html.parser import HTMLParser

_PLACEHOLDER = re.compile(r"<!-- confluence-(?:macro|raw):[^:]*:([A-Za-z0-9+/=]+) -->")


def _enc(xml: str) -> str:
    return base64.b64encode(xml.encode()).decode()


class _ToMarkdown(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.list_stack: list[str] = []
        self.ol_counter: list[int] = []
        self.href: list[str] = []
        self.raw_depth = 0
        self.raw: list[str] = []
        self.raw_name = ""
        self.in_pre = False
        self.table_rows: list[list[str]] = []
        self.cell: list[str] | None = None
        self.in_table = False

    def _emit(self, s: str) -> None:
        if self.cell is not None:
            self.cell.append(s)
        else:
            self.out.append(s)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.raw_depth:
            self.raw_depth += 1
            self.raw.append(self.get_starttag_text() or "")
            return
        if tag.startswith(("ac:", "ri:")):
            self.raw_depth, self.raw = 1, [self.get_starttag_text() or ""]
            self.raw_name = dict(attrs).get("ac:name") or tag
            return
        a = dict(attrs)
        if re.fullmatch(r"h[1-6]", tag):
            self._emit("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "p":
            self._emit("\n\n")
        elif tag in ("strong", "b"):
            self._emit("**")
        elif tag in ("em", "i"):
            self._emit("*")
        elif tag == "code" and not self.in_pre:
            self._emit("`")
        elif tag == "pre":
            self.in_pre = True
            self._emit("\n\n```\n")
        elif tag == "a":
            self.href.append(a.get("href") or "")
            self._emit("[")
        elif tag in ("ul", "ol"):
            self.list_stack.append(tag)
            self.ol_counter.append(0)
            if len(self.list_stack) == 1:
                self._emit("\n")
        elif tag == "li":
            depth = len(self.list_stack) - 1
            if self.list_stack and self.list_stack[-1] == "ol":
                self.ol_counter[-1] += 1
                bullet = f"{self.ol_counter[-1]}."
            else:
                bullet = "-"
            self._emit("\n" + "  " * depth + bullet + " ")
        elif tag == "br":
            self._emit("  \n")
        elif tag == "hr":
            self._emit("\n\n---\n")
        elif tag == "blockquote":
            self._emit("\n\n> ")
        elif tag == "table":
            self.in_table, self.table_rows = True, []
        elif tag == "tr":
            self.table_rows.append([])
        elif tag in ("td", "th"):
            self.cell = []

    def handle_endtag(self, tag: str) -> None:
        if self.raw_depth:
            self.raw_depth -= 1
            self.raw.append(f"</{tag}>")
            if self.raw_depth == 0:
                xml = "".join(self.raw)
                kind = "macro" if "structured-macro" in xml[:40] else "raw"
                marker = f"<!-- confluence-{kind}:{self.raw_name}:{_enc(xml)} -->"
                block_level = kind == "macro" and self.cell is None and not self.list_stack
                self._emit(f"\n\n{marker}\n\n" if block_level else marker)
                self.raw = []
            return
        if re.fullmatch(r"h[1-6]", tag):
            pass
        elif tag in ("strong", "b"):
            self._emit("**")
        elif tag in ("em", "i"):
            self._emit("*")
        elif tag == "code" and not self.in_pre:
            self._emit("`")
        elif tag == "pre":
            self.in_pre = False
            self._emit("\n```")
        elif tag == "a":
            self._emit(f"]({self.href.pop() if self.href else ''})")
        elif tag in ("ul", "ol"):
            self.list_stack.pop()
            self.ol_counter.pop()
        elif tag in ("td", "th"):
            if self.cell is not None and self.table_rows:
                self.table_rows[-1].append("".join(self.cell).strip().replace("\n", " "))
            self.cell = None
        elif tag == "table":
            self.in_table = False
            rows = [r for r in self.table_rows if r]
            if rows:
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]
                lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width] + ["| " + " | ".join(r) + " |" for r in rows[1:]]
                self.out.append("\n\n" + "\n".join(lines) + "\n")

    def handle_data(self, data: str) -> None:
        if self.raw_depth:
            self.raw.append(html.escape(data, quote=False))
            return
        self._emit(data if self.in_pre else re.sub(r"\s+", " ", data))

    def handle_comment(self, data: str) -> None:
        if self.raw_depth:
            self.raw.append(f"<!--{data}-->")


def storage_to_markdown(storage: str | None) -> str:
    """Storage XHTML -> Markdown with macros preserved as placeholders."""
    if not storage:
        return ""
    # CDATA sections inside macros must survive html.parser; protect them as escaped text.
    protected = re.sub(r"<!\[CDATA\[(.*?)\]\]>", lambda m: "&lt;![CDATA[" + html.escape(m.group(1), quote=False) + "]]&gt;", storage, flags=re.DOTALL)
    p = _ToMarkdown()
    p.feed(protected)
    p.close()
    text = "".join(p.out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------------------------------------------------------------- markdown -> storage
def _inline_html(s: str) -> str:
    holders: list[str] = []

    def hold(v: str) -> str:
        holders.append(v)
        return f"\x00{len(holders) - 1}\x00"

    s = _PLACEHOLDER.sub(lambda m: hold(base64.b64decode(m.group(1)).decode()), s)
    s = re.sub(r"`([^`]+)`", lambda m: hold("<code>" + html.escape(m.group(1), quote=False) + "</code>"), s)
    s = html.escape(s, quote=False)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", lambda m: f'<a href="{html.escape(m.group(2))}">{m.group(1)}</a>', s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    s = s.replace("  \n", "<br />")
    return re.sub(r"\x00(\d+)\x00", lambda m: holders[int(m.group(1))], s)


def markdown_to_storage(md: str) -> str:
    """Markdown -> storage XHTML; macro placeholders are restored to their original XML."""
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    list_pat = re.compile(r"^(\s*)(\d+\.|[-*+])\s+(.*)$")
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif _PLACEHOLDER.fullmatch(line.strip()):
            out.append(_inline_html(line.strip()))
            i += 1
        elif line.startswith("```"):
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            out.append("<pre>" + html.escape("\n".join(body), quote=False) + "</pre>")
        elif re.match(r"^#{1,6} ", line):
            level = len(line) - len(line.lstrip("#"))
            out.append(f"<h{level}>{_inline_html(line[level:].strip())}</h{level}>")
            i += 1
        elif re.match(r"^---\s*$", line):
            out.append("<hr />")
            i += 1
        elif line.startswith(">"):
            q: list[str] = []
            while i < len(lines) and lines[i].startswith(">"):
                q.append(lines[i][1:].strip())
                i += 1
            out.append("<blockquote><p>" + _inline_html(" ".join(q)) + "</p></blockquote>")
        elif list_pat.match(line):
            stack: list[tuple[int, str]] = []
            buf: list[str] = []
            while i < len(lines) and (m := list_pat.match(lines[i])):
                indent, marker, text = len(m.group(1)), m.group(2), m.group(3)
                tag = "ol" if marker[0].isdigit() else "ul"
                while stack and indent < stack[-1][0]:
                    buf.append(f"</li></{stack.pop()[1]}>")
                if stack and indent == stack[-1][0]:
                    buf.append("</li>")
                elif not stack or indent > stack[-1][0]:
                    stack.append((indent, tag))
                    buf.append(f"<{tag}>")
                buf.append(f"<li>{_inline_html(text)}")
                i += 1
            while stack:
                buf.append(f"</li></{stack.pop()[1]}>")
            out.append("".join(buf))
        elif line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|?\s*$", lines[i + 1]):
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].startswith("|"):
                if not re.match(r"^\|[\s:|-]+\|?\s*$", lines[i]):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head = "<tr>" + "".join(f"<th>{_inline_html(c)}</th>" for c in rows[0]) + "</tr>"
            body_rows = "".join("<tr>" + "".join(f"<td>{_inline_html(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
            out.append(f"<table><tbody>{head}{body_rows}</tbody></table>")
        else:
            para: list[str] = []
            while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,6} |```|>|\s*(\d+\.|[-*+])\s+|---\s*$|\|)", lines[i]):
                para.append(lines[i])
                i += 1
            out.append("<p>" + _inline_html("\n".join(para)) + "</p>")
    return "".join(out)
