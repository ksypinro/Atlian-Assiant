"""Rich-text request bodies for writes: Markdown (the default for agents) -> ADF / storage."""
from __future__ import annotations

from typing import Any

from ...core.errors import InvalidRequestError
from ...formats.adf import adf_to_markdown, markdown_to_adf
from ...formats.storage import markdown_to_storage, storage_to_markdown
from ...models.common import BodyInput


def _norm(body: BodyInput | str) -> BodyInput:
    return body if isinstance(body, BodyInput) else BodyInput(format="markdown", value=body)


def adf_body(body: BodyInput | str) -> dict[str, Any]:
    """Jira v3 rich text: an ADF document built from Markdown, plain text, storage XHTML or an ADF dict."""
    b = _norm(body)
    if b.format == "adf":
        if not isinstance(b.value, dict):
            raise InvalidRequestError("adf body must be a dict", hint="Pass the ADF document as a dict, or use markdown.")
        return b.value
    text = str(b.value)
    if b.format == "storage":
        text = storage_to_markdown(text)
    if b.format in ("plain", "wiki"):
        return {"version": 1, "type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}] if text else []}
    return markdown_to_adf(text)


def storage_body(body: BodyInput | str) -> dict[str, str]:
    """Confluence v2 body: ``{"representation", "value"}`` (storage XHTML from Markdown, or ADF as a JSON string)."""
    import json

    b = _norm(body)
    if b.format == "storage":
        return {"representation": "storage", "value": str(b.value)}
    if b.format == "adf":
        return {"representation": "atlas_doc_format", "value": json.dumps(b.value if isinstance(b.value, dict) else json.loads(str(b.value)))}
    text = str(b.value)
    if b.format == "plain":
        import html

        return {"representation": "storage", "value": f"<p>{html.escape(text)}</p>"}
    return {"representation": "storage", "value": markdown_to_storage(text)}


def body_text(body: BodyInput | str | None) -> str:
    if body is None:
        return ""
    b = _norm(body)
    return adf_to_markdown(b.value) if isinstance(b.value, dict) else str(b.value)
