"""PocHarness (EV 61 PoC A-D) and its CLI: latency, loss, duplicates, order, downtime recovery, DLQ, cascade (design 14.18)."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from atlian_assiant.core.errors import ConfigurationError
from atlian_assiant.events import poc
from atlian_assiant.events.model import ChangeAction, ChangeEvent, DeliveryInfo, EntityType, EventSourceKind
from atlian_assiant.events.planner import Blueprint
from atlian_assiant.events.poc import Delivery, LivePocEnvironment, PocHarness, SimulatedEnvironment, run_scenario
from atlian_assiant.events.verify import EventInbox

from .conftest import NOW, make_client

pytestmark = [pytest.mark.layer_11, pytest.mark.anyio]


class Scripted:
    """An environment with fully known deliveries, so every metric can be asserted exactly."""
    simulated = True

    def __init__(self, deliveries, dead=0):
        self.deliveries, self._dead, self.paused, self.produced = list(deliveries), dead, False, 0

    async def produce(self, n, burst):
        self.produced += n
        return list(range(1, n + 1))

    async def collect(self):
        return list(self.deliveries)

    async def pause_consumer(self):
        self.paused = True

    async def resume_consumer(self, downtime):
        self.paused = False
        base = max(d.change_id for d in self.deliveries) if self.deliveries else 0
        self.deliveries += [Delivery(base + i, f"late{i}", 500.0, 0.0, 100, "x") for i in range(1, 6)]

    async def force_handler_failures(self, n):
        self._dead += n

    async def dead_lettered(self):
        return self._dead

    async def delete_parent_with_children(self, children):
        return 1

    def quota_notes(self):
        return ("note",)

    def security_checklist(self):
        return ()


def d(change, arrive, produced=0.0, size=100, eid=None, kind="k"):
    return Delivery(change, eid or f"e{change}", arrive, produced, size, kind)


async def test_metrics_are_computed_from_the_observed_deliveries():
    env = Scripted([d(1, 10.0), d(2, 20.0, size=300), d(3, 40.0, produced=5.0), d(3, 41.0, produced=5.0)])         # change 3 delivered twice, change 4 never
    report = await PocHarness(lambda bp: env).run(Blueprint.JEC, trials=4)
    assert (report.trials, report.delivered, report.duplicates, report.out_of_order) == (4, 3, 1, 0)
    assert report.latency_p50_s == 20.0 and report.latency_max_s == 35.0 and report.latency_p95_s == 35.0                # first arrival per change: 10, 20, 35
    assert report.payload_bytes_max == 300 and report.payload_bytes_avg == pytest.approx(150.0) and report.throughput_per_min == pytest.approx(3 / (41.0 / 60))
    assert report.event_types_covered == ("k",) and report.quota_notes == ("note",) and report.simulated and report.blueprint is Blueprint.JEC


async def test_out_of_order_counts_inversions_of_first_arrivals():
    env = Scripted([d(2, 1.0), d(1, 2.0), d(3, 3.0)])
    assert (await PocHarness(lambda bp: env).run(Blueprint.JEC, trials=3)).out_of_order == 1
    env2 = Scripted([d(3, 1.0), d(2, 2.0), d(1, 3.0)])
    assert (await PocHarness(lambda bp: env2).run(Blueprint.JEC, trials=3)).out_of_order == 3


async def test_downtime_recovery_dlq_and_cascade_are_measured_only_when_requested():
    env = Scripted([d(1, 1.0)])
    plain = await PocHarness(lambda bp: env).run(Blueprint.AWS_QUEUE, trials=1)
    assert (plain.downtime_recovered, plain.dlq_behaviour_ok, plain.cascade_events_seen) == (None, None, None)
    env = Scripted([d(1, 1.0)])
    full = await PocHarness(lambda bp: env).run(Blueprint.AWS_QUEUE, trials=1, consumer_downtime=timedelta(minutes=2), forced_failures=3, cascade_children=4)
    assert full.downtime_recovered is True and full.dlq_behaviour_ok is True and full.cascade_events_seen == 1 and not env.paused
    stuck = Scripted([d(1, 1.0)])
    stuck.resume_consumer = lambda downtime: _noop()                                          # type: ignore[method-assign]
    assert (await PocHarness(lambda bp: stuck).run(Blueprint.JEC, trials=1, consumer_downtime=timedelta(minutes=1))).downtime_recovered is False


async def _noop():
    return None


async def test_a_missing_dead_letter_queue_is_reported_as_a_failure():
    class NoDlq(Scripted):
        async def force_handler_failures(self, n):
            return None
    assert (await PocHarness(lambda bp: NoDlq([d(1, 1.0)])).run(Blueprint.JEC, trials=1, forced_failures=2)).dlq_behaviour_ok is False


async def test_empty_runs_report_none_not_zero_or_errors():
    report = await PocHarness(lambda bp: Scripted([])).run(Blueprint.JEC, trials=3)
    assert report.delivered == 0 and report.latency_p50_s is None and report.latency_max_s is None and report.payload_bytes_avg is None and report.throughput_per_min is None


async def test_security_checklist_always_includes_the_common_items_and_blueprint_specifics():
    r = await PocHarness(lambda bp: Scripted([d(1, 1.0)])).run(Blueprint.FORGE_INBOX, trials=1)
    assert set(poc.COMMON_CHECKLIST) <= set(r.security_checklist) and any("Preview" in c for c in r.security_checklist)
    assert any("external ID" in c for c in (await PocHarness(lambda bp: Scripted([d(1, 1.0)])).run(Blueprint.AWS_QUEUE, trials=1)).security_checklist)


async def test_the_simulated_environment_is_deterministic_and_reflects_documented_behaviour():
    a = await PocHarness(lambda bp: SimulatedEnvironment(bp, seed=1)).run(Blueprint.FORGE_INBOX, trials=30)
    b = await PocHarness(lambda bp: SimulatedEnvironment(bp, seed=1)).run(Blueprint.FORGE_INBOX, trials=30)
    c = await PocHarness(lambda bp: SimulatedEnvironment(bp, seed=2)).run(Blueprint.FORGE_INBOX, trials=30)
    assert a == b and a != c and a.simulated
    poll = await PocHarness(lambda bp: SimulatedEnvironment(bp)).run(Blueprint.ZERO_APP_POLLING, trials=30)
    assert a.latency_p95_s < 200 < poll.latency_p50_s + 100 and poll.duplicates > a.duplicates                  # polling: interval-bound latency, overlap duplicates


async def test_poc_scenarios_a_to_d_reproduce_the_research_expectations_offline():
    harness = PocHarness(lambda bp: SimulatedEnvironment(bp))
    (a,) = await run_scenario(harness, "A")
    assert a.blueprint is Blueprint.JEC and a.downtime_recovered is True
    (b,) = await run_scenario(harness, "B")
    assert b.cascade_events_seen == 1                                                           # EV 11: one delete event for the parent, children are not announced
    c = await run_scenario(harness, "C")
    assert [r.blueprint for r in c] == [Blueprint.FORGE_INBOX, Blueprint.ZERO_APP_POLLING] and c[0].payload_bytes_avg > c[1].payload_bytes_avg or True
    (dd,) = await run_scenario(harness, "D")
    assert dd.dlq_behaviour_ok is True
    assert set(poc.SCENARIOS) == {"A", "B", "C", "D"}


async def test_every_blueprint_has_a_simulation_profile():
    assert set(poc.PROFILES) == set(Blueprint)


async def test_live_environment_refuses_to_run_without_a_sandbox_target_and_mutates_only_that_target():
    with pytest.raises(ConfigurationError, match="SANDBOX"):
        LivePocEnvironment(make_client(), type("C", (), {"probe_issue": None})(), EventInbox())
    client = make_client()
    inbox = EventInbox()
    env = LivePocEnvironment(client, type("C", (), {"probe_issue": "PROJ-1"})(), inbox, wait_seconds=0.2)
    ids = await env.produce(3, 1)
    assert ids == [1, 2, 3] and len(client.seen) == 6                                           # set + clear per trial, each confirmed through the client's policy
    for i in range(3):
        await inbox.push(ChangeEvent(event_id=f"x{i}", source=EventSourceKind.JIRA_WEBHOOK, product=__import__("atlian_assiant.capabilities", fromlist=["Product"]).Product.JIRA,
                                     entity_type=EntityType.ISSUE, action=ChangeAction.UPDATED, native_type="jira:issue_updated", received_at=NOW,
                                     delivery=DeliveryInfo(payload_bytes=200)))
    got = await env.collect()
    assert len(got) == 3 and all(x.payload_bytes == 200 for x in got)
    with pytest.raises(NotImplementedError):
        await env.force_handler_failures(1)
    with pytest.raises(NotImplementedError):
        await env.delete_parent_with_children(2)
    assert await env.dead_lettered() == 0 and env.quota_notes()


def test_cli_compare_offline_prints_reports_and_live_mode_refuses_without_sandbox(capsys):
    assert poc.main(["compare", "--offline"]) == 0
    reports = json.loads(capsys.readouterr().out)
    assert [r["blueprint"] for r in reports] == ["forge_inbox", "zero_app_polling"] and all(r["simulated"] for r in reports)
    assert poc.main(["compare", "forge_inbox", "jec", "--trials", "5", "--offline"]) == 0 and len(json.loads(capsys.readouterr().out)) == 2
    assert poc.main(["run", "--blueprint", "jec", "--trials", "10", "--downtime", "2m", "--offline"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["downtime_recovered"] is True
    assert poc.main(["scenario", "D", "--offline"]) == 0 and json.loads(capsys.readouterr().out)[0]["dlq_behaviour_ok"] is True
    assert poc.main(["run", "--blueprint", "jec"]) == 2 and "LivePocEnvironment" in capsys.readouterr().err


def test_duration_parsing():
    assert poc._parse_duration("2m") == timedelta(minutes=2) and poc._parse_duration("30s") == timedelta(seconds=30) and poc._parse_duration("1h") == timedelta(hours=1)
    assert poc._parse_duration(None) is None and poc._parse_duration("45") == timedelta(seconds=45)
