"""Synchronous mirror of the async facade (design section 7.9)."""
from __future__ import annotations

import functools
import inspect
from collections.abc import Iterator
from typing import Any

import anyio.from_thread
from anyio.from_thread import BlockingPortal

from ..config import AtlassianConfig
from .client import AsyncAtlassianClient


class SyncProxy:
    """Wraps an async namespace so every coroutine method becomes blocking and async generators become iterators.

    Docstrings and signatures are preserved by ``functools.wraps``.
    """

    def __init__(self, target: Any, portal: BlockingPortal) -> None:
        self._target, self._portal = target, portal

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._target, name)
        if inspect.iscoroutinefunction(attr):
            @functools.wraps(attr)
            def call(*args: Any, **kwargs: Any) -> Any:
                return self._portal.call(functools.partial(attr, *args, **kwargs))
            return call
        if inspect.isasyncgenfunction(attr):
            @functools.wraps(attr)
            def iterate(*args: Any, **kwargs: Any) -> Iterator[Any]:
                agen = attr(*args, **kwargs)
                while True:
                    try:
                        yield self._portal.call(agen.__anext__)
                    except StopAsyncIteration:
                        return
            return iterate
        if getattr(attr, "__namespace__", False) is True or isinstance(attr, AsyncAtlassianClient):
            return SyncProxy(attr, self._portal)
        return attr

    def __dir__(self) -> list[str]:
        return dir(self._target)

    def __repr__(self) -> str:
        return f"<sync {self._target!r}>"


class AtlassianClient(SyncProxy):
    """Blocking client. Same namespaces and methods as ``AsyncAtlassianClient``, without ``await``.

    Example:
        >>> with AtlassianClient.from_env() as atl:
        ...     issue = atl.jira.issues.get("PROJ-123")
    """

    def __init__(self, async_client: AsyncAtlassianClient) -> None:
        self._cm = anyio.from_thread.start_blocking_portal()
        portal = self._cm.__enter__()
        super().__init__(async_client, portal)
        self._async = async_client
        self._closed = False

    @classmethod
    def from_env(cls, **kwargs: Any) -> AtlassianClient:
        from ..factory import ClientFactory

        return cls(ClientFactory().build_async(AtlassianConfig(), **kwargs))

    @classmethod
    def from_profile(cls, name: str, **kwargs: Any) -> AtlassianClient:
        from ..factory import ClientFactory

        return cls(ClientFactory().build_async(AtlassianConfig.from_profile(name), **kwargs))

    @classmethod
    def for_agent(cls, **kwargs: Any) -> AtlassianClient:
        from ..factory import ClientFactory

        return cls(ClientFactory.for_agent(AtlassianConfig(), **kwargs))

    @property
    def async_client(self) -> AsyncAtlassianClient:
        return self._async

    def via(self, backend: Any) -> SyncProxy:
        """Blocking view that sends every call to ONE backend, e.g. ``atl.via("mcp").jira.issues.get(...)``."""
        return SyncProxy(self._async.via(backend), self._portal)

    def with_policy(self, policy: Any) -> SyncProxy:
        """Blocking view under a different access policy."""
        return SyncProxy(self._async.with_policy(policy), self._portal)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._portal.call(self._async.aclose)
        self._cm.__exit__(None, None, None)

    def __enter__(self) -> AtlassianClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
