"""Agent-facing renderers with explicit truncation markers (design section 7.7)."""
from __future__ import annotations

TRUNCATION = "…[truncated {n} chars]"


def truncate(text: str, max_chars: int | None) -> tuple[str, bool]:
    """Cut ``text`` to ``max_chars`` and append an explicit marker saying how much was dropped."""
    if max_chars is None or len(text) <= max_chars:
        return text, False
    cut = len(text) - max_chars
    return text[:max_chars] + TRUNCATION.format(n=cut), True


def to_agent_text(obj: object, max_chars: int = 4000) -> str:
    """Compact Markdown for LLM context windows from any model exposing ``to_agent_text`` or from a plain object."""
    method = getattr(obj, "to_agent_text", None)
    text = method(max_chars) if callable(method) else str(obj)
    return truncate(str(text), max_chars)[0]
