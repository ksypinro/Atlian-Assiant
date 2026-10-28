"""A small HTML tree and CSS-selector engine (stdlib only) used by the browser backend to read a rendered page.

Supported selectors: ``tag``, ``#id``, ``.class``, ``[attr]``, ``[attr=value]`` / ``[attr='value']`` compounds, descendant (space) and child (``>``)
combinators, and comma-separated alternatives. That is enough for ``selectors.yaml`` and it keeps the drift tests deterministic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape as html_escape
from html.parser import HTMLParser

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Node | str] = field(default_factory=list)
    parent: Node | None = None

    def text(self) -> str:
        parts = [c if isinstance(c, str) else c.text() for c in self.children]
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    def inner_html(self) -> str:
        """The element's content as markup (used for rich bodies; ``text()`` would drop the tags)."""
        out = []
        for c in self.children:
            if isinstance(c, str):
                out.append(html_escape(c, quote=False))
            else:
                attrs = "".join(f' {k}="{html_escape(v)}"' for k, v in c.attrs.items())
                out.append(f"<{c.tag}{attrs}>" if c.tag in _VOID else f"<{c.tag}{attrs}>{c.inner_html()}</{c.tag}>")
        return "".join(out)

    def attr(self, name: str) -> str | None:
        return self.attrs.get(name)

    def iter(self) -> list[Node]:
        out: list[Node] = []
        for c in self.children:
            if isinstance(c, Node):
                out.append(c)
                out.extend(c.iter())
        return out

    def select(self, selector: str) -> list[Node]:
        return select(self, selector)


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {k: (v or "") for k, v in attrs}, parent=self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in _VOID:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].children.append(data)


def parse(html: str) -> Node:
    builder = _Builder()
    builder.feed(html)
    builder.close()
    return builder.root


_TOKEN = re.compile(r"""\s*(>|[^\s>]+)""")
_PART = re.compile(r"""(#[\w-]+|\.[\w-]+|\[[^\]]+\]|[A-Za-z][\w-]*|\*)""")


@dataclass
class _Compound:
    tag: str | None = None
    id: str | None = None
    classes: tuple[str, ...] = ()
    attrs: tuple[tuple[str, str | None], ...] = ()


def _compound(token: str) -> _Compound:
    c = _Compound()
    classes: list[str] = []
    attrs: list[tuple[str, str | None]] = []
    rest = token
    for part in _PART.findall(token):
        rest = rest.replace(part, "", 1)
        if part.startswith("#"):
            c.id = part[1:]
        elif part.startswith("."):
            classes.append(part[1:])
        elif part.startswith("["):
            m = re.fullmatch(r"\[\s*([\w:-]+)\s*(?:=\s*(?:'([^']*)'|\"([^\"]*)\"|([^\]]*?)))?\s*\]", part)
            if not m:
                raise ValueError(f"unsupported attribute selector {part!r}")
            attrs.append((m.group(1), next((g for g in m.groups()[1:] if g is not None), None)))
        elif part != "*":
            c.tag = part.lower()
    if rest.strip():
        raise ValueError(f"unsupported selector syntax {token!r}")
    c.classes, c.attrs = tuple(classes), tuple(attrs)
    return c


def _matches(node: Node, c: _Compound) -> bool:
    if c.tag and node.tag != c.tag:
        return False
    if c.id and node.attrs.get("id") != c.id:
        return False
    if c.classes and not set(c.classes) <= set(node.attrs.get("class", "").split()):
        return False
    return all(name in node.attrs and (value is None or node.attrs[name] == value) for name, value in c.attrs)


def _chain(selector: str) -> list[tuple[str, _Compound]]:
    out: list[tuple[str, _Compound]] = []
    combinator = " "
    for tok in _TOKEN.findall(selector):
        if tok == ">":
            combinator = ">"
            continue
        out.append((combinator, _compound(tok)))
        combinator = " "
    return out


def _alternatives(selector: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in selector:
        depth += ch == "["
        depth -= ch == "]"
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    return [p for p in [*parts, cur.strip()] if p]


def _match_chain(node: Node, chain: list[tuple[str, _Compound]], root: Node) -> bool:
    (combinator, compound), rest = chain[-1], chain[:-1]
    if not _matches(node, compound):
        return False
    if not rest:
        return True
    parent = node.parent
    if combinator == ">":
        return parent is not None and parent is not root and _match_chain(parent, rest, root)
    while parent is not None and parent is not root:
        if _match_chain(parent, rest, root):
            return True
        parent = parent.parent
    return False


def select(root: Node, selector: str) -> list[Node]:
    """Descendants of ``root`` matching ``selector``, in document order, without duplicates."""
    chains = [_chain(alt) for alt in _alternatives(selector)]
    return [n for n in root.iter() if any(ch and _match_chain(n, ch, root) for ch in chains)]
