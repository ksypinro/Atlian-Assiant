# Atlian-Assiant: architecture

This document explains **how the code is built and how a call flows through it**. It is written from the source (`atlian_assiant/`, about
40,000 lines in 250 modules). The longer, original design specification, with every table and decision record, is
[`docs/architecture_design.md`](docs/architecture_design.md); this file is the map, that one is the encyclopedia.

* [Part 1: High-level architecture](#part-1-high-level-architecture)
  * [1.1 What the system does](#11-what-the-system-does) · [1.2 System context](#12-system-context) · [1.3 Layers](#13-layers) ·
    [1.4 Package map](#14-package-map) · [1.5 Core abstractions](#15-core-abstractions) · [1.6 Backends](#16-backends) ·
    [1.7 Routing presets](#17-routing-presets) · [1.8 Access policy](#18-access-policy) · [1.9 Agent layer](#19-agent-layer) ·
    [1.10 Event plane](#110-event-plane) · [1.11 Mirror](#111-mirror) · [1.12 Cross-cutting concerns](#112-cross-cutting-concerns) ·
    [1.13 Deployment shapes](#113-deployment-shapes) · [1.14 Build-time tooling](#114-build-time-tooling)
* [Part 2: Low-level sequences](#part-2-low-level-sequences) (15 sequence diagrams)
* [Part 3: Extension points](#part-3-extension-points) · [Part 4: Verification status and known gaps](#part-4-verification-status-and-known-gaps)

---

## Part 1: High-level architecture

### 1.1 What the system does

Jira and Confluence can be reached in many ways: Cloud REST, Data Center REST, the remote MCP server, the GraphQL gateway, three CLIs, the web UI,
Data Lake shares, the org-admin API and Forge. Each one covers a different subset, authenticates differently and fails differently. Atlian-Assiant
puts **one typed client** in front of all of them:

1. **One operation catalog.** 203 named operations (`jira.issue.get`, `confluence.search.cql`, ...), each with an access class (read, search,
   write, delete, manage, analytics, org_admin), plus a generated tier for the rest of the OpenAPI surface.
2. **Many interchangeable backends.** Each backend declares which operations it supports; a router picks one per call and **falls back safely**.
3. **One access policy** in front of every backend: read-only by default, writes previewed and confirmed, credits and risky backends opt-in.
4. **The same operations, three ways:** a Python API (async and blocking), LLM tools (Anthropic / OpenAI / MCP formats) and a CLI.
5. **An event plane** that turns 49 ways of being notified (webhooks, Forge, polling, queues, Slack, email, ...) into one normalized
   `ChangeEvent` stream, always re-reading the real state before acting.

### 1.2 System context

```mermaid
flowchart LR
    subgraph Callers
        APP["Python application / service"]
        AGENT["LLM agent (tool use)"]
        MCPC["MCP client (Claude, IDEs)"]
        HUMAN["Human in a terminal"]
    end

    subgraph AA["Atlian-Assiant (in your process)"]
        API["Client API"]
        TOOLS["Tool registry"]
        MCPS["MCP server"]
        CLI["atlian-assiant CLI"]
        EV["Event plane"]
    end

    subgraph Atlassian["Atlassian surfaces"]
        REST["Cloud REST: Jira v3, Agile, JSM, Confluence v1 + v2"]
        DC["Data Center REST"]
        RMCP["Remote MCP server v2"]
        GQL["GraphQL gateway"]
        CLIS["acli / twg / jira CLIs"]
        UI["Web UI (browser)"]
        LAKE["Data Lake (Delta Sharing)"]
        ORG["Org admin API"]
        FORGE["Forge app (companion)"]
    end

    subgraph Notify["Change notifications"]
        WH["Webhooks / Automation"]
        Q["SQS / Service Bus / Pub/Sub / Kafka"]
        CHAT["Slack / Teams / email"]
    end

    APP --> API
    AGENT --> TOOLS
    MCPC --> MCPS
    HUMAN --> CLI
    TOOLS --> API
    MCPS --> TOOLS
    CLI --> API
    API --> REST & DC & RMCP & GQL & CLIS & UI & LAKE & ORG & FORGE
    WH --> EV
    Q --> EV
    CHAT --> EV
    FORGE --> EV
    EV -->|"fetch canonical state"| API
```

### 1.3 Layers

Every call goes down the same stack. Each layer has one job and a narrow interface to the next one.

```mermaid
flowchart TB
    E["Entry points: AtlassianClient (blocking) / AsyncAtlassianClient / ToolRegistry / MCP server / CLI"]
    F["Facade namespaces: jira, jsm, confluence, graph, raw, webhooks, events, admin, analytics, exports, index, mirror"]
    P["PolicyGuard: access class, credits, cloudId pin, project/space scope, limits, dry-run, confirmation"]
    R["Router: preset order, circuit breaker, backend opt-in gates, safe fallback, source stamping"]
    B["Backends (12): implement typed protocols, declare supported operations"]
    C["Core: HttpTransport, auth, URLs + cloudId, pagination, retry, rate limits, redaction, errors"]
    N["Normalization: models + ADF / storage to Markdown"]
    X["Atlassian"]

    E --> F --> P --> R --> B --> C --> X
    B -.->|"wire JSON"| N -.->|"typed models"| R
```

Rules that hold across layers:

* **Facade methods never talk to Atlassian.** They name an operation and a protocol method and call `Router.call`.
* **Backends never decide policy.** They only answer "can I serve this?" (`supported_operations()`, `gate()`) and raise
  `UnsupportedOperationError` *before sending anything* when they cannot.
* **All models are backend-neutral.** Every result carries a `SourceRef` stamped by the router (backend, site, cloudId).

### 1.4 Package map

| Package | Lines | Responsibility |
|---|---:|---|
| `facade/` | 5,850 | Public API: `AsyncAtlassianClient`, namespaces (mostly generated by `tools/gen_facade.py`), blocking `SyncProxy`, operation index |
| `testing/` | 4,470 | Fakes for every backend: scenario dataset, fake REST server, fake MCP / GraphQL gateway / CLIs / browser / lake / DB |
| `events/` | 8,100 | Event plane: catalog of 49 delivery methods, provisioners, sources, receivers, pipeline, enricher, stores, sinks, relays |
| `backends/` | 8,500 | 12 backends (see 1.6) plus the shared `OpTransport` adapter |
| `core/` | 1,500 | HTTP transport, auth strategies, URL / cloudId resolution, pagination strategies, retry, rate limiting, redaction, errors |
| `models/` | 710 | Pydantic models: `Issue`, `Content`, `Page[T]`, `SourceRef`, ... |
| `protocols/` | 670 | 37 `typing.Protocol` interfaces (`JiraIssueReader`, `ConfluenceSearcher`, ...) |
| `formats/` | 640 | ADF and Confluence storage format to and from Markdown |
| `agent/` | 590 + docs | Tool registry and exporters, MCP server, `describe()`, generated `AGENT_GUIDE.md` / `llms.txt` |
| `mirror/` | 900 | Keep a local copy (SQL, OpenSearch, vector index) in sync |
| `relay/` | 240 | Serverless webhook relays (AWS Lambda, Azure, GCP, Cloudflare Worker) and their IaC |
| top level | 1,770 | `operations.py` (catalog), `capabilities.py` (enums), `routing.py`, `policy.py`, `factory.py`, `matrix.py`, `config.py`, `cli.py` |

### 1.5 Core abstractions

```mermaid
classDiagram
    class Operation {
        id
        product
        access
        idempotent
        consumes_credits
        cloud_only
    }
    class Backend {
        <<protocol>>
        kind
        supported_operations()
        implementation_for(protocol)
        health()
        aclose()
        gate(policy, op) optional
    }
    class BackendFactory {
        <<protocol>>
        kind
        deployments
        availability(config)
        create(config, core)
    }
    class Router {
        call(op, protocol, method, args)
        candidates(op, forced)
        guarded(guard)
        forced(backend)
    }
    class PolicyGuard {
        authorize(op, args, kwargs)
        backend_block(op, backend)
    }
    class SelectionPolicy {
        order_for(op)
    }
    class Namespace {
        _call(op_id, protocol, method)
    }
    class AsyncAtlassianClient
    class HttpTransport {
        send(spec)
        json(spec)
    }
    class RequestSpec {
        method
        surface
        path
        operation
        idempotent
    }
    Namespace <|-- AsyncAtlassianClient
    Namespace --> Router
    Router --> SelectionPolicy
    Router --> PolicyGuard
    Router --> Backend : candidates
    BackendFactory --> Backend : create
    Backend ..> HttpTransport : most backends
    HttpTransport ..> RequestSpec
    Router ..> Operation
```

* **Operation** (`operations.py`, `capabilities.py`): the unit of everything: routing, policy, tools, docs and tests are keyed by its id.
  The 203 curated operations break down as read 104, write 62, manage 18, delete 6, search 5, analytics 4, org_admin 4.
* **Protocols** (`protocols/`): 37 small interfaces. A backend is a bag of *parts*, each implementing some protocols;
  `implementation_of(backend, Protocol)` finds the part.
* **Matrix** (`matrix.py`): which operation rows each backend claims, `full` or `partial`. It mirrors section 11 of the design document, and a test
  keeps them equal.
* **Page[T] and Cursor** (`core/pagination.py`): opaque cursors encode the backend, the pagination strategy and its state, so the next page always
  goes to the backend that produced the first one.
* **ChangeEvent** (`events/model.py`): the normalized event: source, product, entity, action, key / id / version, delivery metadata, `dedup_key`,
  and (after enrichment) `canonical`, the freshly read object.

### 1.6 Backends

Twelve backends are registered in `factory.default_registry()`. They fall into three families by how they reach Atlassian:

```mermaid
flowchart TB
    subgraph HTTP["HTTP backends: HttpTransport (auth, retry, rate limits)"]
        REST["REST (Cloud)<br/>curated components + generated tier"]
        RDC["REST_DC (Data Center)<br/>DcHttpTransport maps surfaces"]
        ORG["ORG_ADMIN<br/>audit events, events-stream"]
        FRG["FORGE<br/>Forge App REST API (event plane only)"]
    end
    subgraph OWN["Own protocol client"]
        MCP["MCP<br/>MCP SDK over streamable HTTP"]
        DB["DATABASE (DC)<br/>read-only curated SQL, opt-in"]
    end
    subgraph OPT["Adapter backends: OpTransport reuses the REST components"]
        GQL["GRAPHQL<br/>stored queries -> REST wire shape"]
        ACLI["ACLI"]
        TWG["TWG"]
        JCLI["JIRA_CLI"]
        BRW["BROWSER<br/>Playwright, selectors.yaml"]
        LAKE["DATA_LAKE<br/>Delta Sharing + DuckDB"]
    end
    RESTC["Shared REST components: JiraRest, AgileRest, JsmRest, ConfluenceRest, writers, normalizers"]
    REST --> RESTC
    OPT --> RESTC
```

**The `OpTransport` trick** (`backends/optransport.py`). The Cloud REST components describe each call as a `RequestSpec` that carries the
operation id. `OpTransport` is an `HttpTransport` look-alike that never does HTTP: it looks the operation up in a handler table and returns JSON
**in the REST wire shape**. So the CLI, GraphQL, browser and Data Lake backends reuse all of the REST parsing, normalization and pagination; each
one only translates a request into its own medium and back:

| Backend | Handler translates a REST request into... | Translation table |
|---|---|---|
| ACLI / TWG / JIRA_CLI | a CLI command line (`CommandMap.render`), then CLI stdout back into REST JSON (`shape`) | `backends/cli/command_maps/*.py` |
| GRAPHQL | a stored GraphQL document plus variables, then the result back into REST JSON (`Binding.adapt`) | `backends/graphql/ops.py` + `queries/*.graphql` |
| BROWSER | a UI page and selectors, read from the rendered DOM | `backends/browser/ops.py` + `selectors.yaml` |
| DATA_LAKE | SQL over shared Delta tables | `backends/data_lake/ops.py` |

An operation with no handler raises `UnsupportedOperationError` before anything runs, which is what makes the router fall back.

**Per-backend notes**

* **REST** also exposes the **generated tier** (`backends/rest_cloud/generated/`): an operation index produced from OpenAPI specs by
  `tools/codegen.py`. Any generated operation is callable by id through `client.index.call(...)` and goes through the same transport and policy.
* **MCP** (`backends/mcp/`) speaks the live Rovo MCP v2 surface recorded in `catalog_v2.json`: about 20 direct tools, plus `executeRead`,
  `executeWrite` and `executeDestructive` over a catalogue of about 316 operations. It resolves the cloudId once, routes each tool directly or
  through the right execute tool, and maps the server's "successful call carrying an error" replies to typed errors.
* **CLI backends** run the third-party binary through `CliRunner` with a **clean environment** (no tokens passed through) and check the installed
  version and flags once, lazily, with `CapabilityProbe`.
* **BROWSER**, **DATA_LAKE** and **DATABASE** have a `gate()`: the router skips them unless the policy opts in (`allow_browser_writes`,
  `ANALYTICS` access, `allow_direct_db`).

### 1.7 Routing presets

A preset is a backend order per operation pattern (exact id, then the longest `prefix.*`, then `*`). From `routing.PRESET_ORDERS`:

| Preset | Default order (`*`) | Notable overrides |
|---|---|---|
| `service` | REST, MCP, GraphQL, ACLI, TWG, jira-cli, browser | `graph.*`: MCP, GraphQL, TWG · `analytics.*`: Data Lake · `admin.*`: org admin |
| `agent` | MCP, REST, TWG, GraphQL, ACLI, jira-cli, browser | `jira.jql.*` and `confluence.content.delete`: REST only |
| `shell` | ACLI, TWG, jira-cli, REST, MCP, browser | `confluence.*`: TWG, REST, MCP, browser |
| `analytics` | REST, MCP | `analytics.*`: Data Lake · `jira.issue.search`: REST |
| `data_center` | REST_DC, jira-cli, browser, database | (chosen automatically when `deployment = data_center`) |

Only backends that were **built** (credentials present, tool installed, extra installed) and that **declare** the operation become candidates.
A small circuit breaker skips a backend after repeated "unavailable" failures for a cool-down period.

### 1.8 Access policy

`AccessPolicy` (`policy.py`) is a frozen value: allowed access classes (default **read + search**), access classes that need confirmation
(write, delete, manage), `allow_credit_consuming`, an optional cloudId / project / space allow-list, result and attachment-size limits, and the
backend opt-ins. `PolicyGuard.authorize` enforces it before routing, in this order:

1. The access class is allowed (for `raw.request`, the *HTTP method* decides: POST/PUT/PATCH count as write, DELETE as delete).
2. Rovo credits were acknowledged, if the operation consumes them.
3. The session's cloudId is allowed.
4. Scope: JQL and CQL are **rewritten** to the allowed projects / spaces, keys are checked, id-only reads that cannot be scoped are refused.
5. Limits are clamped (`limit`, `page_size`, attachment `max_bytes`).
6. Writes: a `PlannedAction` is built. `dry_run=True` returns it without executing. Otherwise the `ConfirmationHandler` decides, and **delete and
   manage always need confirmation**, whatever the policy says. The `agent` preset refuses `AutoApprove`.

### 1.9 Agent layer

* **`ToolRegistry`** (`agent/tools.py`): 58 tools defined over the facade (37 visible under the default read-only policy). `export()` produces
  Anthropic, OpenAI, JSON Schema or MCP tool definitions. `ainvoke()` validates arguments with a generated Pydantic model, calls the facade (so
  policy and routing apply), and renders a bounded text answer wrapped as *data* (`<atlassian-data ...>` with a preamble telling the model not to
  follow instructions inside it). Errors come back as `ToolResult(ok=False)` with a hint; nothing raises into the agent loop.
* **MCP server** (`agent/mcp_server.py`): `tools/list` and `tools/call` backed by the registry, with read-only and destructive hints.
  stdio or HTTP; HTTP requires a bearer token and refuses non-loopback binds unless explicitly allowed.
* **Discovery**: `client.describe()` explains what is configured and why not; `client.index.discover(query)` searches all operations, including
  the generated tier; the generated `AGENT_GUIDE.md` / `llms.txt` describe the tools for model context.

### 1.10 Event plane

```mermaid
flowchart LR
    subgraph Provision["Provisioners (plan, confirm, apply, verify, teardown)"]
        PV["Jira webhooks · Automation rules · Forge app · AWS / Azure / GCP queues · JEC · Slack / Teams · tunnels · relays"]
    end
    subgraph In["Sources and receivers"]
        RCV["Receivers (push): Jira / DC / Connect / Forge / Automation webhooks<br/>verify, normalize, receipt dedup, enqueue, 202"]
        LQ["Local durable queue"]
        SRC["Sources (pull): JQL / CQL polling · reconciler · Forge inbox · org events-stream · SQS · Slack socket · IMAP · RSS · ..."]
    end
    subgraph Pipe["EventPipeline"]
        D["dedup"] --> FI["filters"] --> EN["CanonicalEnricher<br/>fresh read, version gate, tombstones"] --> H["handlers"]
    end
    subgraph Out["Sinks"]
        S["Kafka · Redis · webhook forward · SSE / WebSocket · Slack / Teams / email / SMS · mirror"]
    end
    PV -.->|"creates"| RCV
    PV -.->|"creates"| SRC
    RCV --> LQ --> D
    SRC --> D
    H --> S
    EN -->|"client.jira.issues.get / confluence.content.get"| API["Client API"]
    H -.->|"failure"| DLQ["dead letter + source.nack"]
```

* **Catalog** (`events/catalog.py`): 49 delivery methods (DM-01 to DM-49: 11 machine push, 11 machine pull, 15 human channels, 8 transports,
  2 guards, 1 in-Atlassian, 1 live UI) and 15 cross-cutting rules. Each entry names its provisioner, source or receiver, and the research section it
  comes from; `events/planner.py` recommends methods for your constraints (no public URL, Confluence needed, AWS approved, ...).
* **Principle:** an event is a *hint*. The enricher always re-reads the issue or page through the client, drops out-of-order deliveries with a
  per-entity version store, and turns "not found" into a tombstone.
* **`EventPlane`** (`events/plane.py`) assembles provisioners, an ASGI receiver app, sources and the pipeline from a plan:
  `provision()`, `verify()`, `run()`, `teardown()`.

### 1.11 Mirror

`client.mirror.jira(store, jql=...)` and `.confluence(store, cql=...)` keep a local copy of a scope: `initial_load()`, `apply_event()` (plug it in as an
event-plane sink) and a nightly `reconcile()` that tombstones deleted items and refreshes changed ones. Stores: in-memory, SQL (SQLite /
PostgreSQL), OpenSearch, and a vector store (`HashingEmbedder` or your own embedder; Chroma, pgvector, Weaviate, Pinecone adapters). A version store
makes every write idempotent.

### 1.12 Cross-cutting concerns

| Concern | Where | How |
|---|---|---|
| Errors | `core/errors.py` | One hierarchy (`AtlassianError` with `hint`, `retryable`, `operation`, `backend`). HTTP status maps to `NotFoundError`, `PermissionDeniedError`, `RateLimitedError`, ... Router control flow uses `UnsupportedOperationError` and `BackendUnavailableError`. |
| Retries | `core/retry.py` | Only idempotent requests, only retryable statuses, exponential backoff with jitter, honouring `Retry-After`. |
| Rate limits | `core/ratelimit.py` | Concurrency limit per API family, `RateLimit-*` header observer, and a per-issue write serializer (no two concurrent writes to one issue). |
| Auth | `core/auth/` | Basic (email + token), service-account bearer, OAuth 2.0 (3LO) with refresh-token rotation, DC PAT. A credential is only applied to surfaces it supports. |
| URLs and cloudId | `core/urls.py` | Site URLs or `api.atlassian.com/ex/{product}/{cloudId}`. The cloudId is resolved once and can be pinned. Absolute URLs are only allowed on the configured site. |
| Secrets | `core/redaction.py`, `core/telemetry.py` | Known secrets and auth headers are redacted from logs, telemetry and error messages. |
| Formats | `formats/` | ADF to and from Markdown, storage format to Markdown, so agents work in Markdown. |
| Config | `config.py`, `events/config.py` | Pydantic settings from `ATLASSIAN_*` environment / `.env` / named profiles with secret references. |

### 1.13 Deployment shapes

| Shape | What runs | Typical use |
|---|---|---|
| Library | `AtlassianClient` in your process | scripts, services, ETL |
| Agent tools | `client.tools.export()` + your LLM loop | custom agents |
| MCP server | `atlian-assiant mcp serve` (stdio or HTTP) | Claude Desktop, IDEs, other MCP clients |
| Webhook receiver | `EventPlane.asgi_app()` behind your web server | public-URL webhooks |
| Private worker | `EventPlane.run()` with pull sources | no inbound internet (polling, Forge inbox, queues, Slack socket) |
| Serverless relay | `relay/` handlers on Lambda / Azure / GCP / Cloudflare | receive publicly, deliver into a private queue |
| Forge companion | `forge_templates/event-inbox` deployed to the site | Jira + Confluence events without a public URL |

### 1.14 Build-time tooling

Generated artifacts are committed and checked in CI, so drift fails the build:

| Tool | Produces / checks |
|---|---|
| `tools/gen_facade.py` | the facade namespaces (`facade/_generated_*.py`) from one table of methods |
| `tools/gen_stubs.py` | `facade/sync.pyi`, so the blocking client is fully typed |
| `tools/docgen.py` | docstring lint (every public method documents Args / Returns / Raises / Access / Example, and examples run) and the generated docs |
| `tools/codegen.py` | the generated REST tier from OpenAPI specs (`--fetch` gets the real specs; a nightly workflow opens a PR on drift) |
| `tools/api_surface.py` | `docs/api_surface.json`: the public API may grow but not lose anything by accident |
| `tools/gql_schema_subset.py` | the recorded GraphQL schema subset, and validation of every stored query against it |

---

## Part 2: Low-level sequences

Participants use the real class names. `Svc` means `CoreServices` / `HttpTransport`.

### 2.1 Building a client

`ClientFactory.build_async` (`factory.py`). Nothing is contacted at build time; backends are built only if they *could* work.

```mermaid
sequenceDiagram
    autonumber
    participant U as Caller
    participant CF as ClientFactory
    participant CFG as AtlassianConfig
    participant CS as CoreServices
    participant REG as BackendRegistry
    participant BF as BackendFactory (each)
    participant R as Router
    participant C as AsyncAtlassianClient

    U->>CF: build_async(config, preset, policy, confirm)
    CF->>CFG: AtlassianConfig() reads ATLASSIAN_* and .env
    CF->>CS: CoreServices.create(config)
    Note over CS: shared httpx client, auth strategy, URL resolver,<br/>cloudId resolver, limiter, write serializer, telemetry
    CF->>REG: factories() (built-ins + entry points "atlian_assiant.backends")
    loop every registered backend kind
        CF->>BF: deployment supported?
        CF->>BF: availability(config)
        alt available (credentials, binary on PATH, extra installed)
            CF->>BF: create(config, core)
            BF-->>CF: backend
        else not available
            Note over CF: BuildReport.skipped[kind] = reason (shown by describe())
        end
    end
    CF->>CF: preset = argument or config (data_center forces DATA_CENTER)
    CF->>CF: SelectionPolicy.from_preset(preset)
    CF->>CF: AccessPolicy.from_config(config) (default read + search)
    alt preset is agent and confirm is AutoApprove
        CF-->>U: ConfigurationError (agents must confirm with a human)
    end
    CF->>R: Router(backends, selection, PolicyGuard(policy, confirm))
    CF->>C: AsyncAtlassianClient(router, config, core, report)
    C->>C: create namespaces jira, confluence, graph, ... index, mirror, tools
    C-->>U: client
```

### 2.2 A read call through the blocking client

`atl.jira.issues.get("PROJ-1")` with the default `service` preset, served by Cloud REST.

```mermaid
sequenceDiagram
    autonumber
    participant U as Caller
    participant SP as SyncProxy
    participant BP as BlockingPortal (event loop thread)
    participant NS as JiraIssues namespace
    participant R as Router
    participant G as PolicyGuard
    participant B as RestCloudBackend
    participant J as JiraRest component
    participant T as HttpTransport
    participant A as Jira Cloud

    U->>SP: atl.jira.issues.get("PROJ-1")
    SP->>SP: attribute is a coroutine function: wrap it
    SP->>BP: portal.call(get, "PROJ-1")
    BP->>NS: await get("PROJ-1")
    NS->>R: call(op jira.issue.get, JiraIssueReader, "get_issue", "PROJ-1")
    R->>R: cloud-only check
    R->>G: authorize(op, args, kwargs)
    G-->>R: GuardDecision (args unchanged, no plan)
    R->>R: candidates = preset order filtered by built and supported_operations()
    R->>B: implementation_of(backend, JiraIssueReader)
    B-->>R: JiraRest part
    R->>G: backend_block(op, backend) (no gate on REST)
    R->>J: get_issue("PROJ-1")
    J->>T: json(RequestSpec GET /issue/PROJ-1, operation=jira.issue.get)
    T->>A: GET /rest/api/3/issue/PROJ-1 (see 2.4)
    A-->>T: 200 JSON
    T-->>J: dict
    J->>J: normalize.issue(): ADF description to Markdown, typed Issue
    J-->>R: Issue
    R->>R: breaker.record_success, stamp_source(Issue, rest, site, cloudId)
    R-->>NS: Issue
    NS-->>BP: Issue
    BP-->>SP: Issue
    SP-->>U: Issue (source.backend = rest)
```

### 2.3 Fallback and failure semantics in `Router.call`

The router only moves to the next backend when the current one **provably sent nothing**.

```mermaid
sequenceDiagram
    autonumber
    participant R as Router
    participant G as PolicyGuard
    participant B1 as Backend 1 (e.g. MCP)
    participant B2 as Backend 2 (e.g. REST)
    participant CB as CircuitBreaker

    R->>G: authorize()
    alt policy denies
        G-->>R: PolicyDeniedError (raised to caller, nothing sent)
    end
    R->>R: forced = backend= argument, or the backend pinned in the cursor, or client.via()
    R->>B1: method(args)
    alt UnsupportedOperationError or BackendUnavailableError (nothing was sent)
        B1-->>R: error
        R->>CB: record_failure(B1)
        alt forced backend
            R-->>R: stop: NoBackendAvailableError with the attempt list
        else not forced
            R->>B2: method(args)
            B2-->>R: result
            R->>CB: record_success(B2)
        end
    else domain answer (404, 403, 400, 409)
        B1-->>R: NotFoundError / PermissionDeniedError / ...
        R-->>R: re-raise to the caller (a fact, not an outage)
    else error after a non-idempotent request was sent
        B1-->>R: error
        R-->>R: re-raise (never risk a duplicate write)
    else success
        B1-->>R: result
        R->>R: stamp_source(result, B1)
    end
```

### 2.4 Inside `HttpTransport.send`

`core/http.py`. Shared by REST, REST_DC, org admin and Forge.

```mermaid
sequenceDiagram
    autonumber
    participant Cmp as REST component
    participant T as HttpTransport
    participant CR as CloudIdResolver
    participant L as ConcurrencyLimiter
    participant S as IssueWriteSerializer
    participant Au as AuthStrategy
    participant H as httpx
    participant O as RateLimitObserver
    participant RP as RetryPolicy

    Cmp->>T: send(RequestSpec)
    opt gateway URLs and cloudId unknown
        T->>CR: resolve() (once, cached)
    end
    T->>L: acquire slot for the API family
    T->>S: lock serialize_key (writes to one issue run one at a time)
    loop until success or final error
        T->>T: build request: URL from surface + path (cloudId pin checked)
        T->>Au: apply(request) (only on supported surfaces)
        T->>H: send
        H-->>T: response
        T->>O: observe(RateLimit-* headers)
        alt 401 and not yet refreshed
            T->>Au: refresh() (OAuth refresh-token rotation)
        else status below 400
            T-->>Cmp: response
        else retryable status and idempotent request
            T->>RP: delay(attempt, Retry-After)
            T->>T: sleep, attempt + 1
        else final error
            T-->>Cmp: raise error_for_status (NotFound, PermissionDenied, RateLimited, ...)
        end
    end
```

### 2.5 A write: dry run, then confirmed execution

`atl.jira.issues.transition("PROJ-1", "Done")` with `access=read,search,write`.

```mermaid
sequenceDiagram
    autonumber
    participant U as Caller or agent
    participant R as Router
    participant G as PolicyGuard
    participant H as ConfirmationHandler
    participant B as Backend (REST)
    participant A as Jira

    U->>R: transition("PROJ-1", "Done", dry_run=True)
    R->>G: authorize()
    G->>G: access WRITE allowed, scope and limits checked
    G->>G: _plan() builds PlannedAction (target, summary, arguments, reversible)
    G-->>R: GuardDecision(plan)
    R-->>U: PlannedAction (nothing was sent)

    U->>R: transition("PROJ-1", "Done")
    R->>G: authorize()
    G->>G: _plan()
    alt WRITE in policy.confirm, or access is DELETE or MANAGE (always)
        alt no handler configured
            G-->>U: ConfirmationDeclinedError (hint: preview with dry_run=True)
        else handler configured
            G->>H: confirm(plan)
            alt declined
                H-->>G: false
                G-->>U: ConfirmationDeclinedError (hint: do not retry)
            else approved
                H-->>G: true
            end
        end
    end
    G-->>R: GuardDecision(no plan)
    R->>B: transition_issue("PROJ-1", "Done")
    B->>A: GET transitions, then POST transition (not idempotent: no retry, no fallback after send)
    A-->>B: 204
    B-->>U: None
```

### 2.6 Paging with a cursor

```mermaid
sequenceDiagram
    autonumber
    participant U as Caller
    participant R as Router
    participant B as Backend that served page 1
    participant PS as PaginationStrategy

    U->>R: search_issues(jql, limit=50)
    R->>B: search_issues(jql, limit=50)
    B->>PS: first(spec, 50)
    B->>PS: parse(response) gives items, next state, total
    B-->>U: Page(items, next_cursor = Cursor(backend, strategy, state).encode())
    U->>R: search_issues(jql, limit=50, cursor=next_cursor)
    R->>R: cursor_backend(cursor) pins the same backend (no fallback)
    R->>B: search_issues(jql, cursor)
    B->>PS: next(spec, state) (nextPageToken, startAt, _links.next, CQL start, GraphQL endCursor, ...)
    B-->>U: Page(next items, next_cursor or None)
```

### 2.7 MCP backend call

`backends/mcp/backend.py`, against the live Rovo MCP v2 server.

```mermaid
sequenceDiagram
    autonumber
    participant R as Router
    participant M as McpJira part (McpClient)
    participant T as SdkMcpTransport
    participant S as mcp.atlassian.com/v2/mcp

    R->>M: list_comments("PROJ-1")
    M->>T: list_tools() (once per session)
    T->>S: initialize + tools/list (Basic auth with the scoped MCP token)
    S-->>T: about 20 direct tools incl. executeRead / executeWrite / executeDestructive
    M->>M: route("listJiraIssueComments"): not direct, so executeRead (from catalog_v2.json)
    opt cloudId not configured
        M->>T: call getAccessibleAtlassianResources
        T-->>M: resources, matched by site URL
    end
    M->>T: call executeRead {cloudId, name: listJiraIssueComments, inputs: {issueIdOrKey}}
    T->>S: tools/call
    S-->>T: text: {"data": {...}} or {"error": true, ...} or a plain error sentence
    T-->>M: McpToolResult
    alt error envelope says "Use executeDestructive instead"
        M->>T: retry once with the named execute tool
    else "Unknown operation"
        M-->>R: UnsupportedOperationError (router falls back to REST)
    else other error
        M-->>R: typed error (PermissionDenied with an admin hint, AuthenticationError for missing scope, ...)
    else data
        M->>M: payload() unwraps "data", normalize to Page[Comment]
        M-->>R: Page[Comment]
    end
```

### 2.8 CLI backend call (acli, twg, jira-cli)

`backends/cli/`. The REST component issues a normal `RequestSpec`; the CLI transport turns it into a command.

```mermaid
sequenceDiagram
    autonumber
    participant J as JiraRest component (shared)
    participant CT as CliTransport (OpTransport)
    participant P as CapabilityProbe
    participant CM as CommandMap
    participant CR as CliRunner
    participant X as acli binary

    J->>CT: send(RequestSpec POST /search/jql, op=jira.issue.search)
    opt first call of the session
        CT->>P: probe()
        P->>CR: run(version args) e.g. --version or "version"
        P->>CR: run(prefix --help) for every mapped command
        P->>P: keep commands whose usage line names the command and whose flags are advertised
        CT->>CT: drop unsupported operations
    end
    alt operation has no handler
        CT-->>J: UnsupportedOperationError (router falls back)
    end
    CT->>CM: select(op, path) and render(spec)
    CM-->>CT: argv (+ private temp files for long text, never in argv)
    CT->>CR: run(argv)
    CR->>X: subprocess with a CLEAN environment (no tokens)
    X-->>CR: stdout / exit code
    CR-->>CT: CliResult (non-zero exit becomes a typed error)
    CT->>CT: parse: JSON, unwrap envelope (twg), or read a count sentence
    CT->>CM: shape(wrap, data, spec) to the REST envelope (offset / token / cql / link pages)
    CT-->>J: httpx.Response(200, REST-shaped JSON)
    J->>J: same normalizer and pagination as REST
```

### 2.9 GraphQL stored query and schema check

`backends/graphql/`.

```mermaid
sequenceDiagram
    autonumber
    participant J as JiraRest component (shared)
    participant OT as GraphQLOpTransport
    participant BD as Binding (ops.py)
    participant D as Document (queries/*.graphql)
    participant GC as GraphQLClient
    participant GW as GraphQL gateway

    J->>OT: send(RequestSpec POST /search/jql, op=jira.issue.search)
    OT->>BD: captures(spec) for each binding of the operation
    OT->>GC: cloud_id()
    BD-->>OT: variables(spec, captures, cloudId) e.g. jql, first, after = nextPageToken
    OT->>D: text + experimental opt-ins (header line "experimental: JiraIssueSearch")
    OT->>GC: run(document, variables, experimental_apis)
    GC->>GW: POST /gateway/api/graphql with X-ExperimentalApi header
    GW-->>GC: {data, errors}
    alt errors
        GC-->>J: typed error (NotFound, PermissionDenied, BetaHeaderOptIn as InvalidRequest, ...)
    else data
        GC-->>OT: data
        OT->>BD: adapt(data, spec): issueSearchStable edges to REST issues, endCursor to nextPageToken
        OT-->>J: REST-shaped JSON
    end

    Note over OT,GW: health(): one __type introspection per request (the gateway refuses more),<br/>only for the types named in the requires header<br/>operations whose fields are missing or deprecated are dropped
```

### 2.10 Agent tool call through the MCP server

```mermaid
sequenceDiagram
    autonumber
    participant L as MCP client (LLM host)
    participant S as MCP server (agent/mcp_server.py)
    participant TR as ToolRegistry
    participant F as Facade method
    participant R as Router + PolicyGuard

    L->>S: tools/list
    S->>TR: definitions of tools visible under the policy
    S-->>L: tools with input schemas, readOnly / destructive hints
    L->>S: tools/call jira_get_issue {key: "PROJ-1"}
    S->>TR: ainvoke(name, arguments)
    alt unknown tool or hidden by policy
        TR-->>S: ToolResult(ok=False, hint)
    end
    TR->>TR: validate arguments with the generated Pydantic model
    TR->>F: call (forced arguments applied)
    F->>R: route (policy, backend choice, fallback)
    R-->>F: Issue
    F-->>TR: Issue
    TR->>TR: render to bounded text, wrap in a data preamble and atlassian-data tags,<br/>add the governance notice if any
    TR-->>S: ToolResult(ok, text, data, source)
    S-->>L: CallToolResult(text + structured content, isError)
```

### 2.11 Webhook ingestion and event processing

Receiver front half (`events/receivers/base.py` `QueueEndpoint.handle`) and worker back half (`events/pipeline.py`, `events/enrich.py`).

```mermaid
sequenceDiagram
    autonumber
    participant A as Atlassian webhook
    participant E as QueueEndpoint (ASGI)
    participant V as Verifier chain
    participant RS as Receipt store
    participant Q as Local queue
    participant P as EventPipeline
    participant DS as Dedup store
    participant EN as CanonicalEnricher
    participant C as Client API
    participant H as Handlers
    participant SK as Sinks

    A->>E: POST /hooks/jira (JSON body)
    E->>E: path, method, size checks (404 / 405 / 413)
    E->>V: verify (HMAC, static header, JWT, Connect JWT, Forge token, IP allow-list, replay window)
    alt verification fails
        E-->>A: 401 (not retried by Atlassian)
    end
    E->>E: parse JSON (400 if invalid), normalize to ChangeEvent
    E->>RS: seen(dedup_key)?
    alt duplicate delivery
        E-->>A: 200 (Atlassian stops retrying)
    end
    E->>Q: depth below max? put(event)
    alt queue full or down
        E-->>A: 429 or 503 (Atlassian retries)
    end
    E-->>A: 202 (fast, before any processing)

    P->>Q: next event (LocalQueueSource)
    P->>DS: seen(dedup_key)?
    alt duplicate
        P->>Q: ack
    end
    P->>P: filters (product, entity, action, custom)
    P->>EN: enrich(event)
    EN->>C: jira.issues.get(key) or confluence.content.get(id)
    alt NotFound
        EN-->>P: tombstone event (action = deleted)
    else older version than already seen
        EN-->>P: None (stale: ack and skip)
    else fresh
        EN-->>P: event with canonical object and version
    end
    P->>H: run matching handlers
    alt a handler fails
        P->>P: dead letter + source.nack (retried later, never lost)
    else success
        P->>DS: mark(dedup_key)
        P->>Q: ack
        P->>SK: publish (a sink outage never re-runs handlers)
    end
```

### 2.12 Polling source with checkpoint and overlap

`events/sources/polling.py` (`JqlPollingSource`; `CqlPollingSource` is the same with `lastmodified`). The transport can be REST, ACLI, MCP or GraphQL.

```mermaid
sequenceDiagram
    autonumber
    participant P as EventPipeline
    participant S as JqlPollingSource
    participant CP as CheckpointStore
    participant C as Client (search)

    loop every interval (default 120 s)
        P->>S: events()
        S->>CP: load(stream)
        alt first run
            S->>CP: save(now) (no history replay)
        else has checkpoint
            S->>S: since = checkpoint minus overlap (clock skew, minute-granular JQL)
            S->>S: jql = (base) AND updated >= "since" ORDER BY updated ASC, key ASC<br/>formatted in the API user's own time zone
            loop each page
                S->>C: search_issues(jql, limit, cursor)
                C-->>S: Page[Issue]
                S->>S: one ChangeEvent per issue, dedup key = id + updated
                S->>CP: save(newest updated) after every page
                S-->>P: events (overlap duplicates are absorbed by dedup)
            end
        end
    end
    Note over S: deletes are invisible to polling:<br/>ReconcilerSource diffs the id set periodically and emits deletions
```

### 2.13 Mirror: initial load, live update, reconcile

`mirror/jira.py` (Confluence is the same shape).

```mermaid
sequenceDiagram
    autonumber
    participant U as Caller or event plane
    participant M as JiraMirror
    participant C as Client
    participant VS as MirrorVersionStore
    participant ST as MirrorStore (SQL / OpenSearch / vector)

    U->>M: initial_load()
    loop iter_search(jql)
        M->>C: next issue
        M->>VS: newer than stored version?
        alt newer
            M->>ST: upsert(document: summary, status, Markdown description, metadata)
        end
    end

    U->>M: apply_event(event) (plugged in as a sink)
    M->>C: jira.issues.get(key) (fresh state, never the payload)
    alt NotFound or delete event
        M->>ST: tombstone(key)
    else
        M->>VS: newer?
        M->>ST: upsert or skip as stale
    end

    U->>M: reconcile() (nightly)
    M->>C: iter_search(jql) gives the current id set
    M->>ST: known_ids(scope)
    M->>ST: tombstone ids that vanished
    M->>ST: upsert new or changed, count unchanged
```

### 2.14 Forge inbox (private network, no public URL)

```mermaid
sequenceDiagram
    autonumber
    participant J as Jira / Confluence
    participant FA as Forge app (event-inbox template)
    participant FS as Forge Storage
    participant FB as ForgeBackend (App REST API)
    participant SRC as ForgeInboxSource
    participant P as EventPipeline

    J->>FA: product trigger (issue updated, page created, ...)
    FA->>FA: filter (ignoreSelf, expressions)
    FA->>FS: append compact record (source, id, timestamp)
    loop every interval, outbound HTTPS only
        SRC->>FB: fetch inbox page from the stored cursor
        FB-->>SRC: records + nextCursor
        SRC->>SRC: skip records already marked processed, normalize the rest (forge_change_event)
        SRC->>SRC: save nextCursor as the checkpoint
        SRC-->>P: events
        alt handled
            P->>SRC: ack(event)
            SRC->>FB: mark the record processed
        else failed
            P->>SRC: nack(event)
            Note over SRC,FS: record stays unprocessed and is pulled again
        end
    end
```

### 2.15 Slack Socket Mode source

```mermaid
sequenceDiagram
    autonumber
    participant JS as Jira Cloud for Slack app
    participant SL as Slack
    participant SC as SocketModeClient (slack_sdk)
    participant SRC as SlackSocketModeSource
    participant P as EventPipeline

    JS->>SL: post "PROJ-1 updated by Ann" in an allowed channel
    SRC->>SC: first receive(): connect() (outbound WebSocket, no public URL)
    SL-->>SC: events_api envelope
    SC-->>SRC: envelope
    SRC->>SC: acknowledge(envelope_id) (otherwise Slack redelivers)
    alt not a message, other channel, or our own bot
        SRC->>SRC: ignore and count
    else allowed channel message
        SRC->>SRC: AtlassianLinkParser finds keys and page links, dedup key = channel + ts
        SRC-->>P: ChangeEvent(s) (a hint: the enricher reads the real issue)
    end
    Note over SRC,SC: aclose(): disconnect() + close()
```

---

## Part 3: Extension points

| You want to... | Do this |
|---|---|
| **Add a backend** | Implement the parts (protocols) and a `BackendFactory` (`kind`, `deployments`, `availability`, `create`). Register it with `BackendRegistry.register()` or ship it as a package with an entry point in the group `atlian_assiant.backends`. Declare its cells in `matrix.py` and section 11 of the design; `tests/contract/test_matrix_matches_design.py` keeps them equal. |
| **Reuse REST logic over a new medium** | Subclass `OpBackend` and give `OpTransport` a handler per operation that returns REST-shaped JSON (as the CLI and GraphQL backends do). |
| **Add an operation** | Add it to `operations.py` (id, access class, idempotency), a protocol method, the REST implementation and a facade entry in `tools/gen_facade.py`; then regenerate (`gen_facade`, `gen_stubs`, `docgen --write`). |
| **Change routing** | `SelectionPolicy.from_preset(preset, overrides={"jira.issue.search": (BackendKind.MCP, BackendKind.REST)})`, or `client.via("mcp")` for one call. |
| **Plug in confirmation** | Any object with `async confirm(PlannedAction) -> bool` (`CallbackConfirmation`, `CliConfirmation`, your UI). |
| **Add an event source or sink** | A source implements `events()`, `ack()`, `nack()`, `aclose()`; a sink implements `publish(event)`. Register new delivery methods in `events/catalog.py`. |
| **Add a mirror store** | Implement `MirrorStore` (`upsert`, `tombstone`, `known_ids`, ...) or a `VectorIndex` + `Embedder`. |

---

## Part 4: Verification status and known gaps

Verified live against a Jira + Confluence Cloud site (October 2026): REST reads, MCP v2 reads, 13 GraphQL read queries, acli and twg command
maps, the blocking client including `index.discover`, and a mirror initial load. Everything else (writes, the event plane, browser, Data
Center, Data Lake, org admin, Forge, jira-cli) is tested against the fakes in `atlian_assiant/testing/` only. See the README status table.

Known gaps worth knowing when reading the code:

* The generated REST tier ships with a **hand-written seed subset** of the OpenAPI specs (48 operations); `python -m tools.codegen --fetch`
  (and the nightly workflow) replaces it with the full specs.
* `client.browser` on the facade is a placeholder that raises a clear error; the browser is reachable as a **backend** through routing.
* The jira-cli backend cannot authenticate yet: the CLI reads `JIRA_API_TOKEN`, and `CliRunner` deliberately strips tokens from the environment.
* Several backends contain flags or field names that are only as good as their last live check; each one re-checks itself at run time
  (`CapabilityProbe` for CLIs, schema `health()` for GraphQL, the recorded catalogue for MCP) and degrades to "unsupported" rather than failing.
