"""Base class for facade namespaces."""
from __future__ import annotations

from typing import Any

from .. import operations as ops
from ..routing import Router


class Namespace:
    """A group of related operations. Every method routes through the Router (policy, backend choice, fallback)."""

    __namespace__ = True
    _OPS: dict[str, str] = {}

    def __init__(self, router: Router) -> None:
        self._router = router

    async def _call(self, op_id: str, protocol: type, method: str, /, *args: Any, backend: Any = None, **kwargs: Any) -> Any:
        cursor = kwargs.get("cursor")
        return await self._router.call(ops.get(op_id), protocol, method, *args, backend=backend,
                                       **({k: v for k, v in kwargs.items() if k != "cursor"}), **({"cursor": cursor} if cursor else {}))
