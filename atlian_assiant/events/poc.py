"""PocHarness (design 14.18, EV section 61): reproduce PoC A (JEC), B (Forge Jira + Confluence), C (private retrieval: Forge App REST vs polling) and D (Automation -> SNS -> SQS).

* ``PocEnvironment`` is the seam: ``LivePocEnvironment`` mutates ONLY the configured sandbox targets (needs WRITE + confirmation, through the client);
  ``SimulatedEnvironment`` is a deterministic offline model used by CI and ``python -m atlian_assiant.events.poc compare --offline``.
* All metrics are computed from the observed deliveries, never taken from the environment's own claims.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import timedelta
from statistics import median
from typing import Any, Protocol

import anyio

from .planner import Blueprint


@dataclass(frozen=True)
class PocReport:
    blueprint: Blueprint
    trials: int
    delivered: int                        # loss = trials - delivered
    duplicates: int
    out_of_order: int
    latency_p50_s: float | None
    latency_p95_s: float | None
    latency_max_s: float | None
    payload_bytes_avg: float | None       # payload size / richness
    payload_bytes_max: int | None
    throughput_per_min: float | None
    downtime_recovered: bool | None       # events produced while the consumer was stopped arrived after restart
    dlq_behaviour_ok: bool | None         # forced handler failures land in the DLQ (AWS PoC D)
    cascade_events_seen: int | None       # parent delete with children (Forge PoC B)
    event_types_covered: tuple[str, ...]  # which mutation kinds produced events
    quota_notes: tuple[str, ...]          # Automation executions, Forge invocations, API points
    security_checklist: tuple[str, ...]   # items for the human security approval
    simulated: bool = False


@dataclass(frozen=True)
class Delivery:
    change_id: int                        # sequence number of the mutation that caused it
    event_id: str
    arrived_at: float                     # seconds on the environment's clock
    produced_at: float
    payload_bytes: int
    native_type: str


class PocEnvironment(Protocol):
    simulated: bool

    async def produce(self, n: int, burst: int) -> list[int]: ...
    async def collect(self) -> list[Delivery]: ...
    async def pause_consumer(self) -> None: ...
    async def resume_consumer(self, downtime: timedelta) -> None: ...
    async def force_handler_failures(self, n: int) -> None: ...
    async def dead_lettered(self) -> int: ...
    async def delete_parent_with_children(self, children: int) -> int: ...
    def quota_notes(self) -> tuple[str, ...]: ...
    def security_checklist(self) -> tuple[str, ...]: ...


COMMON_CHECKLIST = ("callback/relay URLs pass WebhookUrlPreflight", "verifier chain configured (signature, IP allow-list, replay window)", "secrets live in a secret store, not in templates",
                    "probe mutations only touch the configured sandbox targets", "retention is short and queues have dead-letter handling", "least-privilege scopes / IAM reviewed")
EXTRA_CHECKLIST = {Blueprint.JEC: ("JSM Premium/Enterprise entitlement confirmed", "JEC API key stored as a secret reference"),
                   Blueprint.FORGE_INBOX: ("Forge App REST (Preview) enabled by the site admin", "ignoreSelf on; minimal scopes"),
                   Blueprint.FORGE_NATIVE: ("ignoreSelf on; no unnecessary egress",),
                   Blueprint.AWS_QUEUE: ("queue policy bound to the topic ARN", "IAM role uses the Automation external ID")}


def _percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def _inversions(arrival_order: Sequence[int]) -> int:
    return sum(1 for i in range(len(arrival_order)) for j in range(i + 1, len(arrival_order)) if arrival_order[i] > arrival_order[j])


class PocHarness:
    """Runs a blueprint against an environment and measures it (EV section 61: latency, loss, duplicates, order, payload richness, downtime recovery, DLQ, cascade)."""

    def __init__(self, environment_for: Callable[[Blueprint], PocEnvironment]) -> None:
        self._env_for = environment_for

    async def run(self, blueprint: Blueprint, *, trials: int = 20, burst: int = 1, consumer_downtime: timedelta | None = None, forced_failures: int = 0,
                  cascade_children: int = 0) -> PocReport:
        env = self._env_for(blueprint)
        await env.produce(trials, burst)
        deliveries = await env.collect()
        by_change: dict[int, list[Delivery]] = {}
        for d in deliveries:
            by_change.setdefault(d.change_id, []).append(d)
        firsts = [min(v, key=lambda d: d.arrived_at) for v in by_change.values()]
        latencies = [d.arrived_at - d.produced_at for d in firsts]
        arrival = [d.change_id for d in sorted(firsts, key=lambda d: d.arrived_at)]
        span_min = (max((d.arrived_at for d in deliveries), default=0.0) - min((d.produced_at for d in deliveries), default=0.0)) / 60
        downtime_ok: bool | None = None
        if consumer_downtime is not None:
            await env.pause_consumer()
            before = len({d.change_id for d in await env.collect()})
            await env.produce(max(1, min(5, trials)), 1)
            await env.resume_consumer(consumer_downtime)
            downtime_ok = len({d.change_id for d in await env.collect()}) >= before + max(1, min(5, trials))
        dlq_ok: bool | None = None
        if forced_failures:
            before_dlq = await env.dead_lettered()
            await env.force_handler_failures(forced_failures)
            dlq_ok = await env.dead_lettered() - before_dlq == forced_failures
        cascade = await env.delete_parent_with_children(cascade_children) if cascade_children else None
        types = tuple(sorted({d.native_type for d in deliveries}))
        sizes = [d.payload_bytes for d in deliveries]
        return PocReport(blueprint=blueprint, trials=trials, delivered=len(by_change), duplicates=len(deliveries) - len(by_change), out_of_order=_inversions(arrival),
                         latency_p50_s=median(latencies) if latencies else None, latency_p95_s=_percentile(latencies, 0.95), latency_max_s=max(latencies, default=None),
                         payload_bytes_avg=sum(sizes) / len(sizes) if sizes else None, payload_bytes_max=max(sizes, default=None),
                         throughput_per_min=len(by_change) / span_min if span_min > 0 else None, downtime_recovered=downtime_ok, dlq_behaviour_ok=dlq_ok,
                         cascade_events_seen=cascade, event_types_covered=types, quota_notes=env.quota_notes(),
                         security_checklist=(*COMMON_CHECKLIST, *EXTRA_CHECKLIST.get(blueprint, ())), simulated=env.simulated)

    async def compare(self, *blueprints: Blueprint, trials: int = 20) -> list[PocReport]:
        return [await self.run(b, trials=trials) for b in blueprints]


# ---------------------------------------------------------------------------------------------------------------------
# PoC scenarios A-D (EV section 61)
# ---------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class PocScenario:
    key: str
    title: str
    blueprints: tuple[Blueprint, ...]
    trials: int = 20
    consumer_downtime: timedelta | None = None
    forced_failures: int = 0
    cascade_children: int = 0


SCENARIOS: dict[str, PocScenario] = {
    "A": PocScenario("A", "JEC: JSM -> LAN script (EV 58)", (Blueprint.JEC,), consumer_downtime=timedelta(minutes=2)),
    "B": PocScenario("B", "Forge Jira + Confluence triggers incl. cascade delete (EV 56)", (Blueprint.FORGE_INBOX,), cascade_children=3),
    "C": PocScenario("C", "Private retrieval: Forge App REST inbox vs JQL/CQL polling (EV 59)", (Blueprint.FORGE_INBOX, Blueprint.ZERO_APP_POLLING), trials=30),
    "D": PocScenario("D", "Automation -> SNS -> SQS incl. DLQ behaviour (EV 57)", (Blueprint.AWS_QUEUE,), forced_failures=3),
}


async def run_scenario(harness: PocHarness, key: str) -> list[PocReport]:
    sc = SCENARIOS[key]
    return [await harness.run(b, trials=sc.trials, consumer_downtime=sc.consumer_downtime, forced_failures=sc.forced_failures, cascade_children=sc.cascade_children)
            for b in sc.blueprints]


# ---------------------------------------------------------------------------------------------------------------------
# Environments
# ---------------------------------------------------------------------------------------------------------------------
#: blueprint -> (p50 s, p95 s, loss rate, duplicate rate, reorder rate, event kinds, mean payload bytes, cascade per-child events?, DLQ?)
PROFILES: dict[Blueprint, tuple[float, float, float, float, float, tuple[str, ...], int, bool, bool]] = {
    Blueprint.DIRECT_WEBHOOK: (4.0, 30.0, 0.0, 0.05, 0.10, ("jira:issue_updated", "comment_created"), 4200, False, False),
    Blueprint.MANAGED_RELAY: (5.0, 35.0, 0.0, 0.05, 0.10, ("jira:issue_updated",), 900, False, True),
    Blueprint.FORGE_NATIVE: (20.0, 170.0, 0.0, 0.02, 0.15, ("avi:jira:updated:issue", "avi:confluence:updated:page"), 1800, False, False),
    Blueprint.FORGE_INBOX: (22.0, 175.0, 0.0, 0.02, 0.12, ("avi:jira:updated:issue", "avi:confluence:updated:page"), 380, False, True),
    Blueprint.FORGE_REMOTE: (18.0, 165.0, 0.0, 0.03, 0.12, ("avi:jira:updated:issue",), 2400, False, False),
    Blueprint.JEC: (6.0, 20.0, 0.0, 0.0, 0.05, ("automation:issue_transitioned",), 700, False, False),
    Blueprint.AWS_QUEUE: (9.0, 40.0, 0.0, 0.04, 0.10, ("automation:issue_created",), 520, False, True),
    Blueprint.AWS_SSM: (12.0, 60.0, 0.0, 0.0, 0.05, ("automation:issue_created",), 520, False, False),
    Blueprint.ZERO_APP_POLLING: (150.0, 290.0, 0.0, 0.30, 0.02, ("poll:issue", "poll:page"), 120, False, False),
    Blueprint.MCP_POLLING: (170.0, 310.0, 0.0, 0.30, 0.02, ("poll:issue",), 120, False, False),
    Blueprint.TUNNEL: (4.5, 32.0, 0.0, 0.05, 0.10, ("jira:issue_updated",), 4200, False, False),
    Blueprint.ORG_AUDIT: (60.0, 200.0, 0.0, 0.0, 0.0, ("audit:user_created",), 600, False, False),
    Blueprint.AUDIT_WEBHOOK: (15.0, 120.0, 0.02, 0.03, 0.20, ("audit:user_created",), 600, False, False),
    Blueprint.DATA_CENTER_WEBHOOK: (1.0, 3.0, 0.0, 0.01, 0.02, ("jira:issue_updated",), 4000, False, False),
    Blueprint.LIVE_FORGE_UI: (2.0, 6.0, 0.0, 0.0, 0.0, ("realtime:publish",), 200, False, False),
    Blueprint.HUMAN_NOTIFICATIONS: (45.0, 600.0, 0.05, 0.10, 0.30, ("email:jira", "slack:message"), 1500, False, False),
}


class SimulatedEnvironment:
    """Deterministic model of each blueprint's documented behaviour (seeded). It is a stand-in so CI can exercise the harness: it proves nothing about a tenant."""

    simulated = True

    def __init__(self, blueprint: Blueprint, *, seed: int = 61) -> None:
        self.bp, self.rng = blueprint, random.Random(f"{seed}:{blueprint.value}")
        self.p50, self.p95, self.loss, self.dup, self.reorder, self.kinds, self.size, _, self.has_dlq = PROFILES[blueprint]
        self._clock, self._seq = 0.0, 0
        self._deliveries: list[Delivery] = []
        self._paused, self._dead = False, 0
        self._held: list[Delivery] = []

    async def produce(self, n: int, burst: int) -> list[int]:
        ids = []
        for i in range(n):
            self._seq += 1
            ids.append(self._seq)
            produced = self._clock + i * (0.0 if burst > 1 else 1.0)
            if self.rng.random() < self.loss:
                continue
            latency = self.p50 * (1 + abs(self.rng.gauss(0, 0.4))) if self.rng.random() > 0.05 else self.p95 * (0.9 + self.rng.random() * 0.2)
            if self.rng.random() < self.reorder:
                latency += self.p95 * self.rng.random()
            for copy in range(2 if self.rng.random() < self.dup else 1):
                d = Delivery(self._seq, f"{self.bp.value}-{self._seq}", produced + latency + copy * 5, produced, int(self.size * (0.8 + 0.4 * self.rng.random())),
                             self.kinds[self._seq % len(self.kinds)])
                (self._held if self._paused else self._deliveries).append(d)
        self._clock += n + 1
        return ids

    async def collect(self) -> list[Delivery]:
        return list(self._deliveries)

    async def pause_consumer(self) -> None:
        self._paused = True

    async def resume_consumer(self, downtime: timedelta) -> None:
        self._paused = False
        shift = downtime.total_seconds()
        self._deliveries += [Delivery(d.change_id, d.event_id, d.arrived_at + shift, d.produced_at, d.payload_bytes, d.native_type) for d in self._held]
        self._held.clear()

    async def force_handler_failures(self, n: int) -> None:
        if self.has_dlq:
            self._dead += n                      # queues with a DLQ keep failed messages

    async def dead_lettered(self) -> int:
        return self._dead

    async def delete_parent_with_children(self, children: int) -> int:
        # Forge and webhooks emit ONE delete event for the parent; children are not announced (EV section 11); the reconciler's diff finds them.
        return 1 if self.bp in (Blueprint.FORGE_INBOX, Blueprint.FORGE_NATIVE, Blueprint.FORGE_REMOTE, Blueprint.DIRECT_WEBHOOK, Blueprint.MANAGED_RELAY) else 0

    def quota_notes(self) -> tuple[str, ...]:
        return {Blueprint.AWS_QUEUE: ("one Automation execution per trial counts against the monthly limit",),
                Blueprint.JEC: ("one Automation execution per trial", "JEC requires JSM Premium/Enterprise"),
                Blueprint.FORGE_INBOX: ("one Forge invocation per event plus one Async Queue consumer invocation",),
                Blueprint.FORGE_NATIVE: ("one Forge invocation per event",),
                Blueprint.ZERO_APP_POLLING: ("one REST request per poll plus hydration reads (API points)",)}.get(self.bp, ())

    def security_checklist(self) -> tuple[str, ...]:
        return ()


class LivePocEnvironment:
    """Mutates ONLY the configured sandbox target through the client (WRITE + confirmation enforced by its policy) and observes an ``EventInbox`` fed by the
    blueprint's real consumer. Time is wall-clock. Pausing the consumer is delegated to the caller via ``pause``/``resume`` callbacks.
    The i-th inbox event is attributed to the i-th mutation: an estimate for latency and loss, not an exact causal match."""

    simulated = False

    def __init__(self, client: Any, config: Any, inbox: Any, *, pause: Callable[[], Awaitable[None]] | None = None, resume: Callable[[], Awaitable[None]] | None = None,
                 wait_seconds: float = 300.0) -> None:
        from .verify import issue_property_trigger
        if not config.probe_issue:
            from ..core.errors import ConfigurationError
            raise ConfigurationError("probe_issue is not configured", hint="Set ATLASSIAN_EVENTS_PROBE_ISSUE to a SANDBOX issue: PoCs never touch anything else.")
        self.client, self.config, self.inbox, self._wait = client, config, inbox, wait_seconds
        self._trigger, self._pause, self._resume = issue_property_trigger(client), pause, resume
        self._produced: dict[int, float] = {}
        self._seq = 0
        self._t0 = time.monotonic()

    async def produce(self, n: int, burst: int) -> list[int]:
        ids = []
        for _ in range(n):
            self._seq += 1
            self._produced[self._seq] = time.monotonic() - self._t0
            await self._trigger(self.config.probe_issue, f"poc-{self._seq}")
            ids.append(self._seq)
        return ids

    async def collect(self) -> list[Delivery]:
        out: list[Delivery] = []
        await anyio.sleep(0)
        with anyio.move_on_after(self._wait):
            while len({d.change_id for d in out}) < len(self._produced):
                out = [Delivery(i + 1, e.event_id, time.monotonic() - self._t0, self._produced.get(i + 1, 0.0), e.delivery.payload_bytes or 0, e.native_type)
                       for i, e in enumerate(self.inbox.events)]
                if len({d.change_id for d in out}) >= len(self._produced):
                    break
                await anyio.sleep(0.5)
        return out

    async def pause_consumer(self) -> None:
        if self._pause:
            await self._pause()

    async def resume_consumer(self, downtime: timedelta) -> None:
        await anyio.sleep(downtime.total_seconds())
        if self._resume:
            await self._resume()

    async def force_handler_failures(self, n: int) -> None:
        raise NotImplementedError("forced failures need a handler under test: wrap your EventPipeline handler and call this from your own harness")

    async def dead_lettered(self) -> int:
        return 0

    async def delete_parent_with_children(self, children: int) -> int:
        raise NotImplementedError("cascade PoC deletes pages: create and delete a disposable parent/children tree in the sandbox space yourself")

    def quota_notes(self) -> tuple[str, ...]:
        return ("record Automation executions, Forge invocations and API-point usage from the admin consoles during the run",)

    def security_checklist(self) -> tuple[str, ...]:
        return ()


# ---------------------------------------------------------------------------------------------------------------------
# CLI: python -m atlian_assiant.events.poc run|compare --offline
# ---------------------------------------------------------------------------------------------------------------------
def _json(report: PocReport) -> dict[str, Any]:
    d = asdict(report)
    d["blueprint"] = report.blueprint.value
    return d


def _parse_duration(text: str | None) -> timedelta | None:
    if not text:
        return None
    unit = {"s": 1, "m": 60, "h": 3600}[text[-1]] if text[-1] in "smh" else 1
    return timedelta(seconds=float(text.rstrip("smh")) * unit)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="atlian_assiant.events.poc", description="Reproduce the EV section 61 proofs of concept")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run one blueprint")
    run.add_argument("--blueprint", required=True, choices=[b.value for b in Blueprint])
    run.add_argument("--trials", type=int, default=20)
    run.add_argument("--downtime", default=None, help="consumer downtime, e.g. 2m")
    run.add_argument("--forced-failures", type=int, default=0)
    run.add_argument("--offline", action="store_true", help="use the deterministic simulation (the only mode without sandbox credentials)")
    cmp_ = sub.add_parser("compare", help="compare blueprints")
    cmp_.add_argument("blueprints", nargs="*", choices=[b.value for b in Blueprint], help="default: forge_inbox zero_app_polling")
    cmp_.add_argument("--trials", type=int, default=30)
    cmp_.add_argument("--offline", action="store_true")
    scen = sub.add_parser("scenario", help="run PoC A, B, C or D")
    scen.add_argument("key", choices=sorted(SCENARIOS))
    scen.add_argument("--offline", action="store_true")
    args = ap.parse_args(argv)
    if not args.offline:
        print("live PoCs need a sandbox probe target and a consumer wired to an EventInbox: build a LivePocEnvironment in code (see events/poc.py). "
              "Pass --offline for the deterministic simulation.", file=sys.stderr)
        return 2
    harness = PocHarness(lambda bp: SimulatedEnvironment(bp))
    if args.cmd == "run":
        reports = [anyio.run(lambda: harness.run(Blueprint(args.blueprint), trials=args.trials, consumer_downtime=_parse_duration(args.downtime), forced_failures=args.forced_failures))]
    elif args.cmd == "compare":
        reports = anyio.run(lambda: harness.compare(*[Blueprint(b) for b in (args.blueprints or ["forge_inbox", "zero_app_polling"])], trials=args.trials))
    else:
        reports = anyio.run(lambda: run_scenario(harness, args.key))
    print(json.dumps([_json(r) for r in reports], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
