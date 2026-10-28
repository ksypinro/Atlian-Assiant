"""Shared helpers for the event-plane tests."""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any, ClassVar

import pytest

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.capabilities import Access, Product
from atlian_assiant.events.model import ChangeAction, ChangeEvent, DeliveryInfo, EntityType, EventSourceKind
from atlian_assiant.policy import AccessPolicy, CallbackConfirmation
from atlian_assiant.testing import FakeBackend

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def make_client(*, confirm: Callable[[Any], bool] | None = None, access: frozenset[Access] | None = None):
    """A client over FakeBackend that may read, write and manage; ``confirm`` decides (default: approve and remember)."""
    seen: list[Any] = []

    def decide(plan: Any) -> bool:
        seen.append(plan)
        return True if confirm is None else confirm(plan)

    policy = AccessPolicy(allowed=access or frozenset({Access.READ, Access.SEARCH, Access.WRITE, Access.MANAGE, Access.DELETE}))
    client = ClientFactory().build_async(AtlassianConfig(site="example.atlassian.net"), backends=[], preset="agent", policy=policy,
                                         confirm=CallbackConfirmation(decide), extra_backends={BackendKind.REST: FakeBackend()})
    client.seen = seen
    return client


def event(event_id: str = "e1", *, entity: EntityType = EntityType.ISSUE, action: ChangeAction = ChangeAction.UPDATED, key: str | None = "PROJ-1",
          entity_id: str | None = None, source: EventSourceKind = EventSourceKind.JIRA_WEBHOOK, product: Product = Product.JIRA,
          version: str | None = None, native: str = "jira:issue_updated") -> ChangeEvent:
    return ChangeEvent(event_id=event_id, source=source, product=product, entity_type=entity, action=action, native_type=native, entity_key=key,
                       entity_id=entity_id, entity_version=version, received_at=NOW, delivery=DeliveryInfo(method_id="DM-03"))


class ListSource:
    """An EventSource over a fixed list; records acks and nacks."""

    kind: ClassVar[EventSourceKind] = EventSourceKind.JIRA_WEBHOOK
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-03"})
    requires_public_ingress: ClassVar[bool] = False

    def __init__(self, events: list[ChangeEvent]) -> None:
        self._events, self.acked, self.nacked = list(events), [], []

    async def events(self) -> AsyncIterator[ChangeEvent]:
        for e in self._events:
            yield e

    async def ack(self, event: ChangeEvent) -> None:
        self.acked.append(event.event_id)

    async def nack(self, event: ChangeEvent, *, error: BaseException) -> None:
        self.nacked.append(event.event_id)

    async def aclose(self) -> None:
        return None


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


def make_rest_client(server, *, confirm: Callable[[Any], bool] | None = None, access: frozenset[Access] | None = None):
    """A client over the REST backend talking to the ScenarioServer (wire-level assertions)."""
    seen: list[Any] = []

    def decide(plan: Any) -> bool:
        seen.append(plan)
        return True if confirm is None else confirm(plan)

    cfg = AtlassianConfig(site="example.atlassian.net", email="ann@example.com", api_token="secret-token-value")
    backend = ClientFactory().backend(BackendKind.REST, cfg, http=server.client())
    policy = AccessPolicy(allowed=access or frozenset({Access.READ, Access.SEARCH, Access.WRITE, Access.MANAGE, Access.DELETE}))
    client = ClientFactory().build_async(cfg, backends=[], preset="agent", policy=policy, confirm=CallbackConfirmation(decide),
                                         extra_backends={BackendKind.REST: backend})
    client.seen = seen
    return client


def make_org_client(server, *, access: frozenset[Access] | None = None, confirm: Callable[[Any], bool] | None = None):
    """A client whose admin namespace talks to the ScenarioServer through the ORG_ADMIN backend."""
    seen: list[Any] = []

    def decide(plan: Any) -> bool:
        seen.append(plan)
        return True if confirm is None else confirm(plan)

    cfg = AtlassianConfig(site="example.atlassian.net", org_id="org-1", org_api_key="key-123456")
    backend = ClientFactory().backend(BackendKind.ORG_ADMIN, cfg, http=server.client())
    policy = AccessPolicy(allowed=access or frozenset({Access.READ, Access.SEARCH, Access.ORG_ADMIN, Access.MANAGE}))
    client = ClientFactory().build_async(cfg, backends=[], preset="service", policy=policy, confirm=CallbackConfirmation(decide),
                                         extra_backends={BackendKind.ORG_ADMIN: backend})
    client.seen = seen
    return client
