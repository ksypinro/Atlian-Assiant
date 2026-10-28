"""Handlers referenced by `package.module:function` strings in the SSM/JEC tests."""
from __future__ import annotations

from atlian_assiant.events.model import ChangeEvent

SEEN: list[ChangeEvent] = []


async def record(event: ChangeEvent) -> None:
    SEEN.append(event)
