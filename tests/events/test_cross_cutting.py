"""Cross-cutting rules CC-01 ... CC-15 (design 14.2): each rule has an executable check; names carry the rule id for `-k cc_NN`."""
from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from atlian_assiant import operations as ops
from atlian_assiant.core.errors import NotFoundError
from atlian_assiant.events import catalog
from atlian_assiant.events.catalog import CROSS_CUTTING, DELIVERY_METHODS, Ingress, MethodKind
from atlian_assiant.events.enrich import CanonicalEnricher
from atlian_assiant.events.model import ChangeAction as A
from atlian_assiant.events.model import DeliveryInfo
from atlian_assiant.events.model import EntityType as E
from atlian_assiant.events.normalize import JIRA_WEBHOOK_EVENTS, classify_forge_event, normalize_jira_webhook
from atlian_assiant.events.receivers import (
    AllOf,
    HmacSignatureVerifier,
    InboundRequest,
    IpAllowlistVerifier,
    JiraWebhookReceiver,
    ReplayWindowVerifier,
    WebhookUrlPreflight,
)
from atlian_assiant.events.references import BY_METHOD, REFERENCES, links_for
from atlian_assiant.events.stores import (
    MemoryDedupStore,
    MemoryLocalQueue,
    MemoryVersionStore,
    SqliteDb,
    SqliteDedupStore,
)
from atlian_assiant.events.stores.base import DedupStore
from atlian_assiant.models.confluence import Content
from atlian_assiant.models.jira import Issue

from .conftest import event

pytestmark = [pytest.mark.layer_11, pytest.mark.anyio]
ROOT = Path(__file__).parents[2] / "atlian_assiant" / "events"


def rule(rule_id: str):
    return next(c for c in CROSS_CUTTING if c.id == rule_id)


def test_cc_01_taxonomy_every_method_has_a_kind_and_an_ingress_consistent_with_it():
    for m in DELIVERY_METHODS:
        assert isinstance(m.kind, MethodKind) and isinstance(m.ingress, Ingress), m.id
        if m.kind is MethodKind.MACHINE_PUSH:
            assert m.ingress in (Ingress.REQUIRED, Ingress.LAN_ONLY), m.id          # push needs a reachable receiver
        if m.kind is MethodKind.GUARD:
            assert m.ingress is Ingress.NOT_APPLICABLE and m.consumer in (None, "atlian_assiant.events.sinks.kafka:KafkaSink"), m.id
        if m.kind is MethodKind.HUMAN:
            assert m.ingress is Ingress.NONE, m.id                                    # human channels are consumed outbound-only
    assert {m.kind for m in DELIVERY_METHODS} == set(MethodKind)
    assert catalog.INGRESS_RANK[Ingress.NONE] < catalog.INGRESS_RANK[Ingress.MANAGED] < catalog.INGRESS_RANK[Ingress.TUNNEL] < catalog.INGRESS_RANK[Ingress.REQUIRED]
    assert rule("CC-01").research_refs == ("EV §1", "EV §2")


def test_cc_02_the_full_jira_webhook_catalog_is_normalized_including_version_option_sprint_and_board_events():
    families = {"issue_created", "comment_created", "worklog_created", "attachment_created", "issuelink_created", "issue_property_set", "issuetype_created",
                "project_created", "project_restored_archived", "version_released", "version_merged", "filter_created", "user_created", "option_voting_changed",
                "option_timetracking_changed", "sprint_started", "sprint_closed", "board_configuration_changed", "jira_expression_evaluation_failed"}
    for name in families:
        assert name in JIRA_WEBHOOK_EVENTS or f"jira:{name}" in JIRA_WEBHOOK_EVENTS or name in {"version_released", "version_merged"}, name
    assert (E.VERSION, A.MERGED) == JIRA_WEBHOOK_EVENTS["version_merged"]
    assert len([k for k in JIRA_WEBHOOK_EVENTS if k.startswith("option_")]) == 7 and len([k for k in JIRA_WEBHOOK_EVENTS if k.startswith("sprint_")]) == 5
    assert len([k for k in JIRA_WEBHOOK_EVENTS if k.startswith("board_")]) == 4


def test_cc_03_delivery_semantics_headers_become_delivery_info_and_the_failed_api_exists():
    body = json.dumps({"webhookEvent": "jira:issue_updated"}).encode()
    ev = normalize_jira_webhook(json.loads(body), {"X-Atlassian-Webhook-Identifier": "id-1", "X-Atlassian-Webhook-Retry": "3", "X-Atlassian-Webhook-Flow": "Primary"},
                                method_id="DM-03", body=body)
    assert isinstance(ev.delivery, DeliveryInfo) and (ev.event_id, ev.delivery.attempt, ev.delivery.flow) == ("id-1", 3, "Primary")
    assert catalog.method("DM-05").consumer.endswith("WebhookFailedReplaySource")
    assert "webhook.jira.dynamic.failed" in ops.CATALOG


async def test_cc_03_response_codes_stay_outside_atlassians_retry_set_only_for_permanent_failures():
    retried = {408, 409, 425, 429, 500, 502, 503, 504}
    ep = JiraWebhookReceiver(MemoryLocalQueue(), verifiers=[HmacSignatureVerifier("k")], max_queue_depth=1)
    forged = (await ep.handle(InboundRequest("POST", "/hooks/jira", {}, b"{}"))).status
    assert forged == 401 and forged not in retried                                    # forged requests must not loop
    ok = JiraWebhookReceiver(MemoryLocalQueue(), max_queue_depth=1)
    await ok.handle(InboundRequest("POST", "/hooks/jira", {"X-Atlassian-Webhook-Identifier": "a"}, b"{}"))
    overloaded = (await ok.handle(InboundRequest("POST", "/hooks/jira", {"X-Atlassian-Webhook-Identifier": "b"}, b"{}"))).status
    assert overloaded in retried


def test_cc_04_forge_event_coverage_for_jira_jira_software_jsm_and_confluence_nouns():
    jira = ["avi:jira:created:issue", "avi:jira:updated:issue", "avi:jira:deleted:issue", "avi:jira:assigned:issue", "avi:jira:viewed:issue",
            "avi:jira:mentioned:issue", "avi:jira:created:comment", "avi:jira:created:worklog", "avi:jira:created:issuelink", "avi:jira:created:attachment",
            "avi:jira:created:project", "avi:jira:created:version", "avi:jira:created:user"]
    software = ["avi:jira-software:created:sprint", "avi:jira-software:started:sprint", "avi:jira-software:closed:sprint", "avi:jira-software:updated:board"]
    jsm = ["avi:jira-service-management:created:request", "avi:jira-service-management:updated:request"]
    confluence = ["avi:confluence:created:page", "avi:confluence:updated:page", "avi:confluence:deleted:page", "avi:confluence:viewed:page", "avi:confluence:moved:page",
                  "avi:confluence:copied:page", "avi:confluence:created:blogpost", "avi:confluence:created:whiteboard", "avi:confluence:created:database",
                  "avi:confluence:created:folder", "avi:confluence:added:label", "avi:confluence:removed:label", "avi:confluence:created:user",
                  "avi:confluence:deleted:group", "avi:confluence:performed:search", "avi:confluence:created:space", "avi:confluence:created:smartlink"]
    for name in (*jira, *software, *jsm, *confluence):
        entity, action = classify_forge_event(name)
        assert entity is not E.UNKNOWN or name.endswith(":search") and action is A.PERFORMED, name
        assert action is not A.OTHER, name


async def test_cc_05_cascading_delete_gap_a_parent_delete_invalidates_known_children():
    versions = MemoryVersionStore()
    pages = {"1": Content(id="1", version=1), "2": Content(id="2", version=1, parent_id="1")}

    async def fetch(cid, kind):
        if cid not in pages:
            raise NotFoundError("gone")
        return pages[cid]
    en = CanonicalEnricher(lambda k: None, fetch, versions)                           # type: ignore[arg-type, return-value]
    for cid in ("1", "2"):
        await en.enrich(event(cid, entity=E.PAGE, key=None, entity_id=cid))
    del pages["1"]                                                                    # Confluence sends one delete event for the parent, none for the child
    gone = await en.enrich(event("d", entity=E.PAGE, key=None, entity_id="1", action=A.DELETED))
    assert gone.canonical.entity_id == "1" and await versions.get("page:2") is None and en.stats.subtree_checks == [("page:1", "page:2")]
    assert rule("CC-05").implemented_by == ("atlian_assiant.events.enrich:CanonicalEnricher",)


async def test_cc_07_the_event_payload_is_never_the_canonical_database():
    async def fetch(key):
        return Issue(id="1", key=key, summary="REAL summary", updated=datetime(2026, 9, 1, tzinfo=UTC))
    en = CanonicalEnricher(fetch, lambda *a: None, MemoryVersionStore())               # type: ignore[arg-type, return-value]
    lying = event("a").model_copy(update={"payload": {"issue": {"fields": {"summary": "FORGED summary"}}}})
    out = await en.enrich(lying)
    assert out.canonical.summary == "REAL summary" and out.payload["issue"]["fields"]["summary"] == "FORGED summary"    # payload is kept for audit only


async def test_cc_08_idempotency_dedup_stores_agree_and_the_sqlite_table_is_processed_events(tmp_path):
    db = SqliteDb(f"sqlite:///{tmp_path}/e.db")
    for store in (MemoryDedupStore(), SqliteDedupStore(db)):
        assert isinstance(store, DedupStore)
        await store.mark("k", ttl=60)
        assert await store.seen("k") and not await store.seen("other")
    assert (await db.run("SELECT name FROM sqlite_master WHERE name='processed_events'")) != []     # EV section 52 table name


@given(st.permutations([1, 2, 3, 4, 5]))
@settings(max_examples=40, deadline=None)
def test_cc_09_ordering_any_delivery_order_ends_at_the_newest_version(order):
    import anyio

    async def scenario() -> tuple[str, int]:
        current = {"v": 0}

        async def fetch(key):                                     # the fresh read always returns the CURRENT state, whatever order events arrive in
            return Issue(id="1", key=key, updated=datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=current["v"]))
        versions = MemoryVersionStore()
        en = CanonicalEnricher(fetch, lambda *a: None, versions)  # type: ignore[arg-type, return-value]
        passed = 0
        for n in order:
            current["v"] = max(current["v"], n)                   # state only moves forward; deliveries arrive shuffled
            if await en.enrich(event(f"e{n}")) is not None:
                passed += 1
        return str(await versions.get("issue:PROJ-1")), passed
    stored, passed = anyio.run(scenario)
    assert stored == (datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=5)).isoformat() and 1 <= passed <= 5


async def test_cc_10_public_webhook_security_chain_blocks_wrong_ip_stale_and_unsigned_requests():
    import hashlib
    import hmac
    now = 1_800_000_000.0
    chain = AllOf(IpAllowlistVerifier(["3.3.3.0/24"]), ReplayWindowVerifier(60, clock=lambda: now), HmacSignatureVerifier("k"))
    ep = JiraWebhookReceiver(MemoryLocalQueue(), verifiers=[chain])
    fresh = json.dumps({"webhookEvent": "jira:issue_updated", "timestamp": int(now * 1000)}).encode()
    stale = json.dumps({"webhookEvent": "jira:issue_updated", "timestamp": int((now - 3600) * 1000)}).encode()

    def post(body, remote="3.3.3.3", key=b"k", ident="i1"):
        sig = "sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest()
        return ep.handle(InboundRequest("POST", "/hooks/jira", {"X-Hub-Signature": sig, "X-Atlassian-Webhook-Identifier": ident}, body, None, remote))
    assert (await post(fresh)).status == 202
    assert [(await post(fresh, remote="9.9.9.9", ident="i2")).status, (await post(stale, ident="i3")).status, (await post(fresh, key=b"x", ident="i4")).status] == [401, 401, 401]
    async def public(host, port):
        return ["8.8.8.8"]
    assert not (await WebhookUrlPreflight(resolver=public, network=False).check("http://hooks.example.com/x")).ok       # HTTPS only


def test_cc_11_private_pull_sources_are_outbound_only_they_never_listen():
    forbidden = {"uvicorn", "starlette", "fastapi", "flask", "aiohttp.web", "http.server", "socketserver"}
    for path in (ROOT / "sources").glob("*.py"):
        tree = ast.parse(path.read_text())
        imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not {i for i in imported if i.split(".")[0] in forbidden or i in forbidden}, path.name
        assert ".bind(" not in path.read_text() and "start_server" not in path.read_text(), path.name
    for m in DELIVERY_METHODS:
        if m.implemented and m.kind is MethodKind.MACHINE_PULL:
            assert m.ingress in (Ingress.NONE, Ingress.NOT_APPLICABLE), m.id
    assert rule("CC-11").implemented_by == ("atlian_assiant.events.stores.base:LocalQueue",)


def test_cc_15_the_documentation_registry_has_official_https_links_for_every_area():
    assert set(REFERENCES) >= {"jira_webhooks", "forge_events", "forge_dynamic_modules", "forge_async_events", "forge_realtime", "forge_app_rest", "org_audit", "automation"}
    for urls in REFERENCES.values():
        for url in urls:
            assert url.startswith("https://") and url.split("/")[2] in {"developer.atlassian.com", "support.atlassian.com"}, url
    assert all(key in REFERENCES for keys in BY_METHOD.values() for key in keys)
    assert "https://developer.atlassian.com/cloud/jira/platform/webhooks/" in links_for("DM-03") and links_for("DM-99") == ()


def test_cc_12_every_recommended_architecture_is_a_named_blueprint_with_methods_and_a_summary():
    from atlian_assiant.events.planner import BLUEPRINT_SUMMARY, Blueprint, EventPlaneFactory
    assert set(BLUEPRINT_SUMMARY) == set(Blueprint) and len(Blueprint) == 16
    for bp in Blueprint:
        plan = EventPlaneFactory.blueprint(bp)
        assert plan.methods and all(catalog.method(m) for m in plan.methods)
    assert rule("CC-12").implemented_by == ("atlian_assiant.events.planner:Blueprint",) and rule("CC-12").implemented


def test_cc_13_the_decision_guide_recommends_and_names_its_reasons():
    from atlian_assiant.capabilities import Product
    from atlian_assiant.events.planner import EventPlaneFactory, EventRequirements
    plan = EventPlaneFactory.recommend(EventRequirements(products=frozenset({Product.JIRA})))
    assert plan.rationale and "DM-38" in plan.reconciliation and plan.methods == ("DM-36",)
    assert rule("CC-13").implemented_by == ("atlian_assiant.events.planner:EventPlaneFactory",)


def test_cc_14_the_poc_harness_reproduces_the_four_research_pocs():
    from atlian_assiant.events.poc import SCENARIOS, PocHarness, PocReport
    assert set(SCENARIOS) == {"A", "B", "C", "D"} and PocHarness and PocReport
    assert rule("CC-14").implemented_by == ("atlian_assiant.events.poc:PocHarness",) and rule("CC-14").implemented


def test_cc_06_the_human_vs_machine_comparison_is_generated_from_the_catalog():
    from atlian_assiant.events import cli
    table = catalog.methods_table()
    assert table.count("\n| DM-") == 49 and "| human |" in table and "| machine_push |" in table and "| machine_pull |" in table and "| guard |" in table
    assert rule("CC-06").implemented_by == ("atlian_assiant.events.catalog:methods_table",) and rule("CC-06").implemented
    assert {k.value for k in catalog.MethodKind} >= {"human", "machine_push", "machine_pull", "transport", "guard", "in_atlassian", "live_ui"}
    assert cli.main(["methods", "--kind", "human"]) == 0


def test_cc_06_the_cli_lists_methods_filtered_by_kind(capsys):
    from atlian_assiant.events import cli
    assert cli.main(["methods", "--kind", "guard"]) == 0
    out = capsys.readouterr().out
    assert "DM-45" in out and "DM-49" in out and "DM-03" not in out
