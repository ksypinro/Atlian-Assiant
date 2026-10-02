"""One-off bootstrap: writes tracking/definitions/phases.yaml and authored/*.yaml.

Existing authored files are never overwritten (they are hand-editable after creation).
Run:  .venv/bin/python scripts/bootstrap_defs.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.track.model import Store, Task  # noqa: E402
from tools.track.seed_design import read_design, section, strip_md, table_rows  # noqa: E402

# ---------------------------------------------------------------- phases
MACHINE = {
    "P0": ["pytest tests/contract -q", "mypy --strict atlassian_client", "python -m tools.track check-coverage",
           "pytest tests/core/test_describe.py -q"],
    "P1": ["pytest tests/agent -q", "python -m tools.docgen --check", "pytest tests/contract -k mcp -q"],
    "P2": ["pytest tests/contract -k write -q", "pytest tests/routing/test_no_duplicate_writes.py -q",
           "pytest tests/policy -q"],
    "P3": ["pytest tests/events/test_research_coverage.py -q", "pytest tests/events/pipeline -q",
           "pytest tests/events/methods -k 'dm_01 or dm_02 or dm_03 or dm_05 or dm_15 or dm_16 or dm_36 or dm_37 or dm_38' -q"],
    "P4": ["pytest tests/events/methods -q -m phase_p4", "python -m atlassian_client.events.poc compare --offline"],
    "P4b": ["pytest tests/events/methods -q -m phase_p4b"],
    "P4c": ["pytest tests/events/test_research_coverage.py -q", "pytest tests/events/methods -q -m phase_p4c"],
    "P5": ["pytest tests/contract/test_matrix_matches_design.py -q"],
    "P6": ["python -m tools.codegen --check-reachability", "python -m tools.docgen --check", "pytest tests/agent/test_mcp_server.py -q"],
}
HUMAN = {
    "P0": ["Reviewer sign-off"],
    "P1": ["Live Claude tool-loop demo against the sandbox answers a multi-product question"],
    "P2": ["Security review of policy / confirmation flow (SEC tasks of the phase)"],
    "P3": ["Live verify_dm_XX run on the sandbox for each P3 method"],
    "P4": ["Credentials provisioned (AWS, Forge, org admin)", "PoC A-D report review", "Security review"],
    "P4b": ["Slack/Teams/Twilio/IMAP sandbox accounts available", "Security review"],
    "P4c": ["Reviewer confirms guards' hints", "Security review"],
    "P5": ["Third-party CLI version pins approved"],
    "P6": ["Release sign-off"],
}


def write_phases(store: Store) -> None:
    text = read_design()
    rows = table_rows(section(text, r"^## 20\. ", r"^## 21\. "))
    phases = []
    for row in rows:
        m = re.match(r"\*\*(P\d\w*) (.+?)\*\*", row[0])
        pid, name = m.group(1), m.group(2)
        phases.append({"id": pid, "name": name, "scope": strip_md(row[1]), "exit_criteria": strip_md(row[2]),
                       "machine_checks": MACHINE[pid], "human_items": HUMAN[pid]})
    path = store.defs / "phases.yaml"
    path.write_text("# Phase gates. Scope and exit criteria mirror design section 20; checks/human items are hand-maintained.\n"
                    + yaml.safe_dump({"phases": phases}, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8")


# ---------------------------------------------------------------- authored tasks
# (id, title, workstream, refs, deliverables, acceptance, deps, size)
def T(id, title, refs, deliv, acc, deps=(), size="M", ws=None, check=None, type="build"):
    phase = id.split(".")[0]
    return dict(id=id, title=title, phase=phase, workstream=ws or id.split(".")[1], design_refs=refs, deliverables=deliv,
                acceptance=acc, depends_on=list(deps), size=size, type=type,
                checks=[check or f"pytest tests/{id.split('.')[1].lower()}/test_{id.lower().replace('.', '_')}.py -q"],
                source="authored")


AUTHORED = [
    # ---- P0 core ----
    T("P0.CORE.001", "Package scaffold: layout, pyproject extras, ruff + mypy strict, py.typed", ["§4", "§19"],
      ["pyproject.toml", "atlassian_client/"], ["Package layout from section 4 exists and imports", "mypy --strict and ruff pass on the empty skeleton",
        "Optional extras declared as in section 19"], check="mypy --strict atlassian_client"),
    T("P0.CORE.002", "capabilities.py: Access, BackendKind, Product, Deployment, Operation", ["§5.1"], ["atlassian_client/capabilities.py"],
      ["Enums and frozen Operation dataclass exactly as specified"], ["P0.CORE.001"], "S"),
    T("P0.CORE.003", "operations.py: canonical operation catalog (all namespaces)", ["§5.2"], ["atlassian_client/operations.py"],
      ["Every operation id in the section 5.2 namespace table exists with access/idempotent/credits/cloud_only metadata",
       "Ids are unique and stable; catalog is the single source for routing, policy, tools and docs"], ["P0.CORE.002"]),
    T("P0.CORE.004", "AtlassianConfig, env vars, profiles.toml, SecretProvider", ["§6"], ["atlassian_client/config.py"],
      ["All settings of section 6.1 parse from env/.env; unset sections disable backends", "SecretStr never appears in repr/describe/logs",
       "from_profile() and secret:// references resolve via providers"], ["P0.CORE.001"]),
    T("P0.CORE.005", "Error hierarchy with LLM-readable hints", ["§7.5"], ["atlassian_client/core/errors.py"],
      ["All error classes of section 7.5 exist with message/hint/status/operation/backend/retryable/details", "Hint table entries implemented"],
      ["P0.CORE.001"], "S"),
    T("P0.CORE.006", "ApiSurface, UrlResolver, CloudIdResolver (+ cloudId pinning)", ["§7.1"], ["atlassian_client/core/urls.py"],
      ["URL forms for basic vs gateway auth for every surface", "cloudId resolution order and per-site cache", "pin_cloud_id enforced client-side"],
      ["P0.CORE.002", "P0.CORE.004"]),
    T("P0.CORE.007", "HttpTransport: auth injection, retry, 401 refresh-once, concurrency, redaction", ["§7.2"], ["atlassian_client/core/http.py"],
      ["RequestSpec/HttpTransport as specified", "Errors mapped per section 7.5", "Bounded concurrency per surface"],
      ["P0.CORE.005", "P0.CORE.006", "P0.CORE.008", "P0.CORE.011"]),
    T("P0.CORE.008", "AuthStrategy protocol + Basic, ServiceAccount, PAT, DcBasic, OrgApiKey, Connect-JWT strategies", ["§7.3"],
      ["atlassian_client/core/auth/"], ["Each strategy sets the documented header and declares supported surfaces"], ["P0.CORE.006"]),
    T("P0.CORE.009", "OAuth 2.0 3LO with refresh-token rotation persisted (0600)", ["§7.3"], ["atlassian_client/core/auth/oauth3lo.py"],
      ["Refresh once on 401", "Rotated refresh token written to oauth_token_store with 0600 permissions"], ["P0.CORE.008"]),
    T("P0.CORE.010", "Pagination strategies + opaque Cursor codec (all 9 strategies)", ["§7.4"], ["atlassian_client/core/pagination.py"],
      ["Offset, NextPageToken, JSM, LinkCursor, CQL, OrgEventsCursor, Cli, Mcp, DcOffset", "Property-based tests: no gaps, no duplicates, cursor round-trip"],
      ["P0.CORE.007"]),
    T("P0.CORE.011", "RetryPolicy, RateLimitObserver, ConcurrencyLimiter, IssueWriteSerializer", ["§7.6"], ["atlassian_client/core/retry.py", "atlassian_client/core/ratelimit.py"],
      ["Retry-After honoured with jitter; non-idempotent only retried on 429", "Per-issue write serialization", "Tolerant rate-limit header parser"],
      ["P0.CORE.005"]),
    T("P0.CORE.012", "Rich-text formats: ADF, Confluence storage, render() with truncation", ["§7.7"], ["atlassian_client/formats/"],
      ["ADF<->Markdown, storage<->Markdown (macro placeholders round-trip), atlas_doc_format", "Golden-file tests"], ["P0.CORE.001"]),
    T("P0.CORE.013", "FieldResolver, UserResolver, SpaceResolver, MetadataCache", ["§7.8"], ["atlassian_client/core/resolvers.py", "atlassian_client/core/cache.py"],
      ["Names resolve to ids; ambiguity raises AmbiguousReferenceError with candidates", "TTL cache keyed by cloudId/surface/path"], ["P0.CORE.007"]),
    T("P0.CORE.014", "BackendFactory protocol, BackendRegistry (entry points), ClientFactory, presets", ["§12.1", "§12.2"], ["atlassian_client/factory.py"],
      ["Registry with plugin entry points", "ClientFactory.build/build_async/backend/sub_client and the five preset constructors"], ["P0.CORE.004", "P0.CORE.015"]),
    T("P0.CORE.015", "Backend base protocol, Router, SelectionPolicy, CircuitBreaker, fallback rules", ["§9.1", "§12.3", "§12.4"],
      ["atlassian_client/protocols/base.py", "atlassian_client/routing.py"],
      ["Fallback only on Unsupported/Unavailable and only when nothing was sent", "Cursor pins backend; forced backend disables fallback",
       "Preset orders from section 12.3"], ["P0.CORE.002", "P0.CORE.005"]),
    T("P0.CORE.016", "AsyncAtlassianClient facade + namespace skeleton + docstring standard", ["§13.1", "§13.2"], ["atlassian_client/facade/"],
      ["Namespace tree of section 13.1 exists", "Every public method follows the section 13.2 docstring template"], ["P0.CORE.014", "P0.CORE.015"]),
    T("P0.CORE.017", "SyncProxy + generated .pyi stubs (AtlassianClient)", ["§7.9"], ["atlassian_client/facade/sync.py"],
      ["Coroutines become blocking; async iterators become iterators; docstrings preserved"], ["P0.CORE.016"]),
    T("P0.CORE.018", "describe() manifest and client.help()", ["§16.1"], ["atlassian_client/agent/describe.py"],
      ["Reports backends ready/unavailable with reasons, access, notes, events status", "Never contains secrets"], ["P0.CORE.016"], check="pytest tests/core/test_describe.py -q"),
    T("P0.CORE.019", "Telemetry, RedactingFilter, optional OpenTelemetry spans", ["§7.10"], ["atlassian_client/core/telemetry.py", "atlassian_client/core/redaction.py"],
      ["Authorization/token/secret values masked; no bodies by default"], ["P0.CORE.001"], "S"),
    T("P0.MODELS.001", "Domain models: common, jira, jsm, confluence, graph, admin, identity, webhooks", ["§8"], ["atlassian_client/models/"],
      ["Page/SourceRef/UserRef/Tombstone/BodyInput + every model listed in section 8", "to_agent_text() truncation marker"], ["P0.CORE.012"]),
    T("P0.PROTO.001", "All protocol definitions in protocols/ (section 9)", ["§9"], ["atlassian_client/protocols/"],
      ["Every Protocol in section 9 exists, runtime_checkable, signatures as specified"], ["P0.MODELS.001", "P0.CORE.015"]),
    T("P0.QA.001", "Contract-test harness parametrized over backends", ["§18"], ["tests/contract/conftest.py"],
      ["A protocol suite runs against every backend that claims the operation"], ["P0.CORE.001"], "S"),
    T("P0.QA.002", "FakeBackend implementing all protocols (+ respx helpers, fixtures)", ["§18"], ["atlassian_client/testing/"],
      ["In-memory implementation of every protocol", "Used by docstring doctests"], ["P0.PROTO.001"]),
    T("P0.REST.001", "REST search-then-hydrate helper (CQL ids -> v2 batch bodies)", ["§10.1", "§7.4"], ["atlassian_client/backends/rest_cloud/"],
      ["Large extraction does not use body expansion in CQL", "Batched fetch with bounded concurrency"], ["BE.rest"]),
    T("P0.DOCS.001", "Docstring lint (required sections, examples runnable as doctests)", ["§16.5", "§18"], ["tools/docgen.py (lint)"],
      ["Missing section fails; examples run against FakeBackend"], ["P0.QA.002"], "S"),
    T("P0.REL.001", "scripts/check.sh and git pre-commit/pre-push hook for tracker + tests", ["§19"], ["scripts/check.sh"],
      ["Runs validate, check-coverage, report --check, verify-done"], [], "S", check="bash scripts/check.sh"),
    # ---- P1 ----
    T("P1.AGENT.001", "@agent_tool, ToolSpec, ToolRegistry, ToolResult, exporters (anthropic/openai/json_schema/mcp)", ["§16.2"], ["atlassian_client/agent/"],
      ["Schemas from signatures; descriptions assembled from metadata", "Errors become ToolResult(ok=False) with hint"], ["P0.CORE.016"]),
    T("P1.AGENT.002", "AccessPolicy, PolicyGuard, ConfirmationHandler, dry-run PlannedAction", ["§16.4"], ["atlassian_client/policy.py"],
      ["Access classes, credits, cloudId, project/space scope (JQL/CQL rewriting), limit clamping", "Writes build PlannedAction"], ["P0.CORE.015"]),
    T("P1.AGENT.003", "for_agent() preset wiring and AGENT_GUIDE / llms.txt content", ["§16.1", "§16.5"], ["atlassian_client/agent/docs/"],
      ["AtlassianClient.for_agent() read-only MCP-first", "Guide outline of section 16.5"], ["P1.AGENT.001", "P1.AGENT.002"]),
    T("P1.MCP.001", "MCP session lifecycle, live tool list, tool map, McpResultMapper", ["§10.3"], ["atlassian_client/backends/mcp/"],
      ["Connect, bind cloudId, build supported_operations from tools/list", "Primary vs deferred (discover/execute*) routing", "Full tool map of section 10.3"], ["BE.mcp"]),
    T("P1.DOCS.001", "docgen: AGENT_GUIDE.md, llms.txt, operations.md, support matrix, DM ledger", ["§16.5"], ["tools/docgen.py"],
      ["Generated from docstrings and registries; --check fails on drift"], ["P1.AGENT.001"]),
    # ---- P2 ----
    T("P2.AGENT.001", "Confirmation handlers: Cli, Callback, AutoApprove (refused for agent preset), DenyAll", ["§16.4"], ["atlassian_client/policy.py"],
      ["AutoApprove cannot be used with preset=agent"], ["P1.AGENT.002"], "S"),
    T("P2.REST.001", "Confluence version-checked updates (n+1, ConflictError hint)", ["§10.1"], ["atlassian_client/backends/rest_cloud/confluence_v2.py"],
      ["409 maps to ConflictError with re-fetch hint"], ["BE.rest"], "S"),
    T("P2.REST.002", "Attachment upload/download streaming with max_bytes and text extraction", ["§10.1", "§9.2"], ["atlassian_client/backends/rest_cloud/"],
      ["Lazy download from metadata; size caps enforced"], ["BE.rest"], "S"),
    T("P2.CORE.001", "No-duplicate-write guarantees across router fallback", ["§12.4"], ["tests/routing/test_no_duplicate_writes.py"],
      ["After a non-idempotent request was sent any error propagates"], ["P0.CORE.015"], "S", check="pytest tests/routing/test_no_duplicate_writes.py -q"),
    # ---- P3 events infra ----
    T("P3.EVENTS.001", "events/catalog.py (DM-01..49, CC-01..15) + vendored research + coverage test", ["§14.19"], ["atlassian_client/events/catalog.py", "docs/research/", "tests/events/test_research_coverage.py"],
      ["Ledger complete and unique; every research section mapped; every component importable; one contract test per method"],
      ["P0.CORE.002"], check="pytest tests/events/test_research_coverage.py -q"),
    T("P3.EVENTS.002", "EventProvisioner base, ProvisionMode, plans, EndToEndProbe, EventPlaneConfig", ["§14.3"], ["atlassian_client/events/provision/base.py", "atlassian_client/events/config.py", "atlassian_client/events/verify.py"],
      ["plan/apply/verify/teardown contract", "Probes mutate only configured sandbox targets and need WRITE + confirmation"], ["P3.EVENTS.001", "P1.AGENT.002"]),
    T("P3.EVENTS.003", "ChangeEvent model, EventSource protocol, normalization tables, AtlassianLinkParser base", ["§14.1", "§14.4", "§14.11"],
      ["atlassian_client/events/model.py", "atlassian_client/events/sources/base.py", "atlassian_client/events/normalize.py"],
      ["Every native event type maps per section 14.11; unknown maps to UNKNOWN/OTHER and is kept"], ["P3.EVENTS.001", "P0.MODELS.001"]),
    T("P3.EVENTS.004", "EventPipeline, CanonicalEnricher (version gate, cascade invalidation), stores, DLQ", ["§14.12", "§14.13"],
      ["atlassian_client/events/pipeline.py", "atlassian_client/events/enrich.py", "atlassian_client/events/stores/"],
      ["Dedup, retry/backoff, DLQ", "404 becomes Tombstone; parent delete invalidates descendants", "Out-of-order delivery harmless"],
      ["P3.EVENTS.003", "P0.CORE.016"], check="pytest tests/events/pipeline -q"),
    T("P3.EVENTS.005", "WebhookEndpoint, verifiers, response semantics, WebhookUrlPreflight, ASGI/FastAPI/Flask adapters", ["§14.5"],
      ["atlassian_client/events/receivers/"],
      ["Status codes per the section 14.5 table", "Preflight rejects private IPs, bad ports, bad TLS, redirects"], ["P3.EVENTS.003"]),
    T("P3.EVENTS.006", "AutomationRuleTemplate, STANDARD_ENVELOPE, PayloadMapping, AutomationRuleProvisioner", ["§14.7"], ["atlassian_client/events/automation.py"],
      ["SNS/SSM/JEC rejected for Confluence", "import_bundle/api/browser/guided strategies"], ["P3.EVENTS.002"]),
    # ---- P4 ----
    T("P4.FORGE.001", "Forge companion app generator (native/inbox/remote/realtime) + ForgeCliRunner + deploy CLI", ["§14.6", "§10.10"],
      ["forge_templates/event-inbox/", "atlassian_client/backends/forge/"],
      ["Manifest, filters, scopes, async limits, storage inbox, App REST endpoints generated", "forge register/deploy/install wrapped"], ["BE.forge", "P3.EVENTS.002"]),
    T("P4.EVENTS.001", "EventPlaneFactory.recommend/blueprint/build + Blueprint enum", ["§14.16"], ["atlassian_client/events/planner.py"],
      ["Decision guide of EV 60 encoded; every blueprint buildable explicitly"], ["P3.EVENTS.004", "P3.EVENTS.002"]),
    T("P4.EVENTS.002", "PocHarness (EV 61 PoC A-D) and CLI", ["§14.18"], ["atlassian_client/events/poc.py"],
      ["Reports latency, loss, duplicates, order, downtime recovery, DLQ, cascade"], ["P4.EVENTS.001"]),
    T("P4.EVENTS.003", "Relay IaC + handlers (aws_lambda, azure_function, gcp_function, cloudflare_worker)", ["§14.8"], ["atlassian_client/relay/"],
      ["Same verifier chain and dedup as local receivers"], ["P3.EVENTS.005"]),
    # ---- P4b / P4c ----
    T("P4b.NOTIFY.001", "AtlassianLinkParser for email/Slack/Teams/SMS/RSS; digest splitting", ["§14.4", "§14.10"], ["atlassian_client/events/human/"],
      ["Issue keys, browse URLs, Confluence ids, JSM keys extracted; unresolved messages still emitted as UNKNOWN"], ["P3.EVENTS.003"]),
    T("P4c.EVENTS.001", "Downstream sinks: Kafka, Redis Streams, SSE, WebSocket, webhook-forward, notify sinks", ["§14.12"], ["atlassian_client/events/sinks/"],
      ["EventSink protocol implementations publish enriched events"], ["P3.EVENTS.004"]),
    # ---- P5 ----
    T("P5.CORE.001", "Matrix-vs-design test: declared supported_operations() equals section 11 cells", ["§11"], ["tests/contract/test_matrix_matches_design.py"],
      ["Disagreement between code and design fails (resolve via DCR or code fix)"], ["P0.CORE.015"], "S", check="pytest tests/contract/test_matrix_matches_design.py -q"),
    T("P5.CLI.001", "CliRunner + CapabilityProbe + command_maps (acli/twg/jira_cli)", ["§10.5", "§10.6", "§10.7"], ["atlassian_client/core/cli_runner.py", "atlassian_client/backends/cli/command_maps/"],
      ["Probe drops mapped operations whose flags are missing in the installed version"], ["P0.CORE.015"]),
    T("P5.BROWSER.001", "Playwright session, versioned selectors.yaml, native exports, evidence screenshots", ["§10.8"], ["atlassian_client/backends/browser/"],
      ["Persistent profile (human does SSO); read-only by default; writes need opt-in + confirmation"], ["BE.browser"]),
    # ---- P6 ----
    T("P6.REST.001", "tools/codegen.py: OpenAPI -> generated REST tier + operations_index.json", ["§10.1"], ["tools/codegen.py", "atlassian_client/backends/rest_cloud/generated/"],
      ["Every operationId of Jira v3, Agile, JSM, Confluence v2/v1 reachable", "Nightly drift job opens a PR"], ["P0.CORE.007"]),
    T("P6.AGENT.001", "discover/call meta-tools over the operation index", ["§16.3"], ["atlassian_client/agent/"],
      ["atlassian_discover_operations and atlassian_call_operation honour policy"], ["P6.REST.001", "P1.AGENT.002"]),
    T("P6.AGENT.002", "MCP server mode (`atlassian-client mcp serve`) and the atlassian-client CLI", ["§16.7", "§16.8"], ["atlassian_client/agent/mcp_server.py", "atlassian_client/cli.py"],
      ["Every curated tool served over stdio/http", "CLI commands of section 16.8"], ["P1.AGENT.001"]),
    T("P6.MIRROR.001", "MirrorStore protocol, JiraMirror, ConfluenceMirror (restrictions mirrored)", ["§15"], ["atlassian_client/mirror/"],
      ["Initial load, incremental via event plane, nightly reconcile with id-set diff"], ["P3.EVENTS.004"]),
    T("P6.MIRROR.002", "Mirror sinks: SQLite, Postgres, OpenSearch, vector stores via Embedder protocol", ["§15"], ["atlassian_client/mirror/sinks/"],
      ["Sinks are adapters for MirrorStore"], ["P6.MIRROR.001"]),
    T("P6.REL.001", "Packaging: extras, entry points, SemVer rules, py.typed, .pyi stubs", ["§19"], ["pyproject.toml"],
      ["Install matrix for each extra imports cleanly"], ["P0.CORE.001"], "S"),
    T("P6.QA.001", "Operation-coverage report and nightly API-drift CI job", ["§18"], ["tools/codegen.py"],
      ["Report shows curated vs generated coverage"], ["P6.REST.001"], "S"),
]


def main() -> None:
    store = Store()
    store.authored.mkdir(parents=True, exist_ok=True)
    write_phases(store)
    created = 0
    for spec in AUTHORED:
        task = Task.model_validate(spec)
        path = store.authored / f"{task.id}.yaml"
        if path.exists():
            continue
        path.write_text(yaml.safe_dump(task.model_dump(mode="json"), sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
        created += 1
    print(f"phases.yaml written; authored tasks created: {created} (total defined {len(AUTHORED)})")


if __name__ == "__main__":
    main()
