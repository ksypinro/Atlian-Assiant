"""EventPlane: provisioners + sources + pipeline assembled from a plan (design 14.16): ``.provision(confirm)``, ``.verify()``, ``.run()``, ``.teardown()``."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import anyio

from ..models.webhooks import WebhookSubscription
from ..policy import ConfirmationHandler
from . import catalog
from .config import EventPlaneConfig
from .enrich import CanonicalEnricher
from .pipeline import EventPipeline
from .planner import EventPlanePlan
from .provision.base import EventProvisioner, VerificationReport
from .receivers.adapters import as_asgi_app
from .receivers.base import WebhookEndpoint
from .receivers.jira import JiraWebhookReceiver
from .sources.base import EventSource, LocalQueueSource
from .sources.failed_replay import WebhookFailedReplaySource
from .sources.org_audit import OrgEventsListSource, OrgEventsStreamSource
from .sources.polling import CqlPollingSource, JqlPollingSource
from .sources.reconciler import ReconcilerSource
from .stores import (
    MemoryVersionStore,
    SqliteCheckpointStore,
    SqliteDb,
    SqliteDeadLetterSink,
    SqliteDedupStore,
    SqliteLocalQueue,
)


@dataclass
class EventPlane:
    plan: EventPlanePlan
    pipeline: EventPipeline
    provisioners: dict[str, EventProvisioner] = field(default_factory=dict)
    sources: dict[str, EventSource] = field(default_factory=dict)
    endpoints: list[WebhookEndpoint] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    config: EventPlaneConfig = field(default_factory=EventPlaneConfig)

    # ---------------------------------------------------------------- assembly
    @classmethod
    def from_plan(cls, plan: EventPlanePlan, client: Any, *, store_url: str, config: Any = None) -> EventPlane:
        cfg: EventPlaneConfig = config or EventPlaneConfig(store_url=store_url)
        db = SqliteDb(store_url)
        checkpoints, queue = SqliteCheckpointStore(db), SqliteLocalQueue(db)
        sources: dict[str, EventSource] = {}
        provisioners: dict[str, EventProvisioner] = {}
        endpoints: list[WebhookEndpoint] = []
        skipped: dict[str, str] = {}
        all_ids = tuple(dict.fromkeys((*plan.methods, *plan.reconciliation)))
        for dm in all_ids:
            method = catalog.method(dm)
            if not method.implemented:
                skipped[dm] = f"phase {method.phase} is not implemented yet"
                continue
            if dm in ("DM-02", "DM-03"):
                receiver = JiraWebhookReceiver(queue, method_id=dm, path="/hooks/jira")
                endpoints.append(receiver)
                if "webhook-queue" not in sources:
                    sources["webhook-queue"] = LocalQueueSource(queue)
                if cfg.public_callback_base and dm == "DM-03":
                    from .provision.jira_webhooks import JiraDynamicWebhookProvisioner
                    provisioners[dm] = JiraDynamicWebhookProvisioner(client, callback_url=cfg.public_callback_base.rstrip("/") + receiver.path,
                                                                     subscriptions=[WebhookSubscription(events=["jira:issue_created", "jira:issue_updated", "jira:issue_deleted"])])
            elif dm == "DM-05":
                sources[dm] = WebhookFailedReplaySource.from_client(client, checkpoints)
            elif dm == "DM-36":
                skipped[dm] = "needs a JQL: add JqlPollingSource.from_client(client, jql, checkpoints) explicitly"
            elif dm == "DM-37":
                skipped[dm] = "needs a CQL: add CqlPollingSource.from_client(client, cql, checkpoints) explicitly"
            elif dm == "DM-38":
                skipped[dm] = "needs a JQL/CQL scope: add ReconcilerSource.for_jql / for_cql explicitly"
            elif dm == "DM-11":
                sources[dm] = OrgEventsListSource.from_client(client, checkpoints)
            elif dm == "DM-12":
                sources[dm] = OrgEventsStreamSource.from_client(client, checkpoints)
                from .provision.org_audit import OrgAuditProvisioner
                provisioners[dm] = OrgAuditProvisioner(client, checkpoints)
            else:
                skipped[dm] = "provision and consume this method explicitly (it needs method-specific settings)"
        enricher = CanonicalEnricher.from_client(client, MemoryVersionStore())
        pipeline = EventPipeline(list(sources.values()), dedup=SqliteDedupStore(db), enricher=enricher, dead_letter=SqliteDeadLetterSink(db))
        return cls(plan, pipeline, provisioners, sources, endpoints, skipped, cfg)

    # ---------------------------------------------------------------- lifecycle
    async def provision(self, confirm: ConfirmationHandler) -> dict[str, Any]:
        """Print each plan, ask for confirmation, apply. Returns ``{dm_id: outputs}``."""
        out: dict[str, Any] = {}
        for dm, prov in self.provisioners.items():
            plan = await prov.plan()
            out[dm] = dict(await prov.apply(plan, confirm=confirm))
        return out

    async def verify(self, **kw: Any) -> list[VerificationReport]:
        return [await prov.verify(timeout=kw.get("timeout", timedelta(minutes=5))) for prov in self.provisioners.values()]

    def asgi_app(self) -> Callable[..., Awaitable[None]]:
        """The webhook receivers as one ASGI app (mount it behind your relay, tunnel or load balancer)."""
        return as_asgi_app(self.endpoints)

    async def run(self, *, stop: anyio.Event | None = None) -> None:
        await self.pipeline.run(stop=stop)

    async def teardown(self, confirm: ConfirmationHandler) -> None:
        for prov in self.provisioners.values():
            await prov.teardown(confirm=confirm)

    def describe(self) -> dict[str, Any]:
        return {"blueprints": [b.value for b in self.plan.blueprints], "methods": list(self.plan.methods), "reconciliation": list(self.plan.reconciliation),
                "sources": sorted(self.sources), "provisioners": sorted(self.provisioners), "endpoints": [e.path for e in self.endpoints], "skipped": dict(self.skipped)}


__all__ = ["CqlPollingSource", "EventPlane", "JqlPollingSource", "ReconcilerSource"]
