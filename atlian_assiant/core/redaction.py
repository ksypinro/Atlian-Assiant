"""Secret redaction for logs, errors and tool output (design sections 7.10, 17)."""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any

SENSITIVE_KEYS = ("authorization", "token", "api_key", "apikey", "password", "secret", "cookie", "set-cookie", "refresh_token")
MASK = "***"

_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*)(basic|bearer|jwt)\s+[A-Za-z0-9._~+/=\-]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|password|secret)\s*[:=]\s*)[^\s,;&\"']+"),
    re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=\-]{8,}"),
]


class Redactor:
    """Masks configured secret values plus common credential patterns."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self._secrets = {s for s in secrets if s and len(s) >= 4}

    def add(self, secret: str) -> None:
        if secret and len(secret) >= 4:
            self._secrets.add(secret)

    def text(self, value: str) -> str:
        for s in sorted(self._secrets, key=len, reverse=True):
            value = value.replace(s, MASK)
        for pat in _PATTERNS:
            value = pat.sub(lambda m: f"{m.group(1)}{MASK}", value)
        return value

    def mapping(self, data: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in data.items():
            if any(s in k.lower() for s in SENSITIVE_KEYS):
                out[k] = MASK
            elif isinstance(v, Mapping):
                out[k] = self.mapping(v)
            elif isinstance(v, str):
                out[k] = self.text(v)
            else:
                out[k] = v
        return out


class RedactingFilter(logging.Filter):
    def __init__(self, redactor: Redactor | None = None) -> None:
        super().__init__()
        self.redactor = redactor or Redactor()

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redactor.text(record.getMessage())
        record.args = None
        return True
