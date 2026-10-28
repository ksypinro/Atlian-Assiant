"""Contract-test harness: every protocol suite runs against every backend that claims the operations (design 18)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import pytest

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.protocols.base import implementation_of
from atlian_assiant.testing import FakeBackend
from atlian_assiant.testing.server import ScenarioServer

P = TypeVar("P")
BACKENDS = ["fake", "rest"]


@dataclass
class Harness:
    name: str
    backend: Any
    server: ScenarioServer | None

    def impl(self, protocol: type[P]) -> P:
        found = implementation_of(self.backend, protocol)
        assert found is not None, f"{self.name} does not implement {protocol.__name__}"
        return found  # type: ignore[no-any-return]


@pytest.fixture(params=BACKENDS)
def harness(request: pytest.FixtureRequest) -> Harness:
    if request.param == "fake":
        return Harness("fake", FakeBackend(), None)
    server = ScenarioServer()
    cfg = AtlassianConfig(site="example.atlassian.net", email="ann@example.com", api_token="secret-token-value")
    backend = ClientFactory().backend(BackendKind.REST, cfg, http=server.client())
    return Harness("rest", backend, server)


@pytest.fixture()
def impl(harness: Harness) -> Callable[[type[P]], P]:
    return harness.impl
