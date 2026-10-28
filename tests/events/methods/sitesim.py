"""A tiny stand-in for 'Jira/Confluence with a delivery method configured': mutating the probe target delivers a webhook/envelope."""
from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from atlian_assiant.events.receivers.base import InboundRequest, WebhookEndpoint

Deliver = Callable[[str, str], Awaitable[None]]      # (entity ref, what changed) -> deliver to the receiver


def jira_body(key: str, native: str = "jira:issue_updated") -> dict[str, Any]:
    return {"webhookEvent": native, "timestamp": int(time.time() * 1000), "issue": {"id": "10001", "key": key,
            "fields": {"updated": "2026-09-01T12:00:00.000+0000", "project": {"key": key.split("-")[0]}}}}


def envelope(product: str, ref: str, trigger: str) -> dict[str, Any]:
    entity = {"jira": "issue", "confluence": "page"}[product]
    key_field = "entity_key" if product == "jira" else "entity_id"
    return {"v": 1, "atl_event_id": f"{ref}:{time.time_ns()}", "product": product, "trigger": trigger, "entity_type": entity, key_field: ref}


class Post:
    """Delivers a JSON body to a receiver the way Atlassian would."""

    def __init__(self, endpoint: WebhookEndpoint, headers: dict[str, str] | None = None) -> None:
        self.endpoint, self.headers, self.counter = endpoint, headers or {}, 0
        self.statuses: list[int] = []

    async def __call__(self, body: dict[str, Any]) -> None:
        self.counter += 1
        raw = json.dumps(body).encode()
        resp = await self.endpoint.handle(InboundRequest("POST", self.endpoint.path, {"X-Atlassian-Webhook-Identifier": f"id-{self.counter}", **self.headers}, raw))
        self.statuses.append(resp.status)


class _Issues:
    def __init__(self, site: SiteSim) -> None:
        self.site = site

    async def set_property(self, key: str, prop: str, value: Any) -> None:
        self.site.writes.append(("property", key))
        await self.site.on_issue(key)

    async def edit(self, key: str, update: Any) -> None:
        self.site.writes.append(("label", key))
        await self.site.on_issue(key)


class _Labels:
    def __init__(self, site: SiteSim) -> None:
        self.site = site

    async def add(self, content_id: str, labels: Any) -> None:
        self.site.writes.append(("page-label", content_id))
        await self.site.on_page(content_id)

    async def remove(self, content_id: str, label: str) -> None:
        return None


class _Namespace:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


class SiteSim:
    """Quacks like the parts of the client the probes use; every mutation of a watched target triggers a delivery callback."""

    def __init__(self, on_issue: Callable[[str], Awaitable[None]] | None = None, on_page: Callable[[str], Awaitable[None]] | None = None) -> None:
        async def nothing(ref: str) -> None:
            return None
        self.on_issue, self.on_page = on_issue or nothing, on_page or nothing
        self.writes: list[tuple[str, str]] = []
        self.jira = _Namespace(issues=_Issues(self))
        self.confluence = _Namespace(labels=_Labels(self))
        self.webhooks = _Namespace(failed=self._failed)
        self.failed_list: list[Any] = []

    async def _failed(self, after: Any = None) -> list[Any]:
        return self.failed_list


async def run_stack(verify: Callable[..., Awaitable[Any]], *, endpoint: Any, make_site: Callable[[Post], SiteSim], config: Any, timeout: float = 5.0) -> Any:
    """Receiver -> durable queue -> pipeline -> inbox, with a simulated site; runs ``verify`` against it and returns its report."""
    import anyio

    from atlian_assiant.events.pipeline import EventPipeline
    from atlian_assiant.events.sources.base import LocalQueueSource
    from atlian_assiant.events.stores import MemoryDedupStore
    from atlian_assiant.events.verify import EventInbox

    inbox = EventInbox()
    pipeline = EventPipeline([LocalQueueSource(endpoint.queue, poll_interval=0.001)], dedup=MemoryDedupStore())
    inbox.attach(pipeline)
    stop = anyio.Event()
    site = make_site(Post(endpoint))
    from datetime import timedelta
    async with anyio.create_task_group() as tg:
        tg.start_soon(lambda: pipeline.run(stop=stop))
        report = await verify(client=site, config=config, inbox=inbox, timeout=timedelta(seconds=timeout))
        stop.set()
    return report
