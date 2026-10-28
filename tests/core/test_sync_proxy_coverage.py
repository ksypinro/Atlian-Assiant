"""The blocking client must never hand out an un-awaited coroutine: index and mirror were missed (found running the README example)."""
from __future__ import annotations

import inspect

from atlian_assiant import AtlassianClient, AtlassianConfig, ClientFactory
from atlian_assiant.mirror.sinks.memory import InMemoryMirrorStore


def client() -> AtlassianClient:
    return AtlassianClient(ClientFactory().build_async(AtlassianConfig(site="example.atlassian.net")))


def test_index_discover_blocks_and_returns_results():
    with client() as atl:
        hits = atl.index.discover("issue watchers")
        assert not inspect.iscoroutine(hits) and len(hits) > 0


def test_mirror_objects_built_from_the_sync_client_are_blocking_too():
    with client() as atl:
        mirror = atl.mirror.jira(InMemoryMirrorStore(), jql="project = PROJ")
        assert not inspect.iscoroutinefunction(mirror.initial_load) and not inspect.iscoroutinefunction(mirror.reconcile)
