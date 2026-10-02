# Combined Atlassian Integration Research Flow
## Data Access + Event Streaming Architecture

**Research Integration Date:** 2 October 2026  
**Scope:** Atlassian Cloud (Jira, Jira Software, JSM, Confluence)  
**Focus:** Complete integration patterns combining data fetching with event-driven notifications

---

## Executive Summary

This document synthesizes two complementary research areas:
1. **Data Access Research** — how to read/fetch data from Jira and Confluence
2. **Event Streaming Research** — how to be notified of changes in a private network

Together, they form complete **read-and-react** architectures for AI agents, integrations, and custom workflows.

### Key Principle

```
Event arrives (fast signal)
    ↓
Fetch canonical state via REST (authoritative read)
    ↓
Process/index/act
    ↓
Periodic reconciliation (correctness)
```

---

## Part 1: The Three Data Access Layers

### Layer 1: REST APIs (Canonical Source)

| API Family | Primary Use | Strength |
|---|---|---|
| **Jira Platform v3** | Direct issue/field/project reads | Broadest coverage; deterministic |
| **Jira Software** | Agile/board/sprint data | Purpose-built for SW teams |
| **JSM** | Request/knowledge-base operations | Support workflows |
| **Confluence v2** | Page/space/attachment reads | Newest; cursor pagination |
| **Confluence v1 + CQL** | Full-text content search | Rich query language |

**Best for:** deterministic reads, high-volume ETL, scheduled syncs, precise field selection

### Layer 2: Agent-Oriented Tools (Natural Language)

| Tool Set | Coverage | Authorization |
|---|---|---|
| **Atlassian MCP v2** | Jira, Confluence, Teamwork Graph | OAuth or API token + permission groups |
| **Teamwork Graph CLI (TWG)** | Cross-product context | Agent-first; natural language |
| **Atlassian CLI (ACLI)** | Jira-focused terminal operations | OAuth or API token |

**Best for:** AI agents, natural-language workflows, exploratory tasks

### Layer 3: Browser Automation (UI-Only)

| Approach | Coverage | Limitation |
|---|---|---|
| **Integrated browser** | UI-visible data only | Slow; brittle; session management |
| **Computer-use** | One-off exploratory tasks | Poor for production sync |

**Best for:** human one-offs, UI-only workflows, fallback when no API exists

---

## Part 2: The Four Event Notification Patterns

### Pattern A: Direct HTTP Webhooks

```
Atlassian
    ↓ HTTPS POST
Your Public Endpoint
    ↓
Queue
    ↓
Worker processes
```

**Jira support:** ✅ Full event catalog  
**Confluence support:** ❌ No equivalent Cloud REST API  
**Public endpoint required:** ✅ Yes  
**Best for:** operational low-latency integrations when public URL is acceptable

**Key mechanisms:**
- JQL filtering
- 30-day registration expiry (requires refresh)
- Up to 5 retries (~5–15 min apart)
- Deduplication via `X-Atlassian-Webhook-Identifier`

---

### Pattern B: Forge Product Triggers (Atlassian-Hosted)

```
Jira / Confluence event
    ↓
Forge trigger (no outbound required from you)
    ↓
Async Queue / Storage
    ↓
Your code runs in Forge
```

**Jira support:** ✅ Broad event coverage  
**Confluence support:** ✅ Comprehensive events  
**Public endpoint required:** ❌ No  
**Event latency:** ~3 minutes typical  
**Best for:** complete event capture; private network friendly

**Key strengths:**
- Event filtering expressions
- Async execution (up to 15 min for consumers)
- Forge Storage / event inbox pattern
- Optional Realtime WebSockets for live Forge UI

---

### Pattern C: Private Polling (Pull Model)

```
Your Private Worker
    ↓ outbound HTTPS GET
Atlassian API (REST or /events-stream)
    ↓
Process with checkpoint
```

**Jira support:** ✅ JQL incremental search  
**Confluence support:** ✅ CQL + REST v2  
**Audit events:** ✅ Cursor-based `/events-stream`  
**Public endpoint required:** ❌ No  
**Latency:** polling interval (1–10 min typical)  
**Best for:** conservative architectures; no public ingress allowed

**Variants:**
- **REST polling:** Jira JQL with `updated >= ...` checkpoint
- **CQL polling:** Confluence `lastmodified >= ...` checkpoint
- **Audit polling:** `/events-stream` with cursor pagination

---

### Pattern D: Queue-Based Private Delivery

```
Jira Automation
    ↓
AWS SNS / SQS  (or)  Jira Edge Connector queue
    ↓
Your Private Worker polls queue
```

**Setup:** Automation → SNS/SQS or JEC  
**Public endpoint required:** ❌ No  
**Durability:** Queue retries + DLQ  
**Best for:** AWS environments; JSM automation; durable buffering

**Key advantage:** Decouples Atlassian event rate from your worker throughput

---

## Part 3: Integrated Reference Architectures

### Architecture 1: Complete Private Agent (Jira + Confluence)

**Scenario:** AI agent inside a corporate network; no inbound Internet allowed

```
┌─────────────────────────────────────────┐
│         ATLASSIAN CLOUD                 │
├─────────────────────────────────────────┤
│                                         │
│  Jira / Confluence                      │
│     ↓                                   │
│  Forge triggers (event capture)         │
│     ↓                                   │
│  Forge Async Queue / Storage            │
│     ↓                                   │
│  (Optional: Forge App REST API)         │
│                                         │
└─────────────────────────────────────────┘
        ↑
        │ outbound HTTPS only
        │
┌─────────────────────────────────────────┐
│    PRIVATE CORPORATE NETWORK            │
├─────────────────────────────────────────┤
│                                         │
│  Event Collector                        │
│   ├─ fetch Forge event inbox            │
│   ├─ dedup by event ID                  │
│   └─ enqueue locally                    │
│                                         │
│  REST Enrichment                        │
│   ├─ JQL search on demand               │
│   ├─ getJiraIssue (MCP or REST)         │
│   └─ getConfluenceContent (MCP or REST) │
│                                         │
│  Search Index                           │
│   ├─ store full-text content            │
│   ├─ denormalize relationships          │
│   └─ vector embeddings                  │
│                                         │
│  Custom AI Agent                        │
│   ├─ query local index                  │
│   ├─ generate response                  │
│   └─ optional: write back via REST      │
│                                         │
└─────────────────────────────────────────┘
```

**Data flow:**
1. **Event detection:** Forge triggers capture `avi:jira:updated:issue` → async queue
2. **Storage:** Forge stores compact event records (source, ID, timestamp)
3. **Collection:** Private worker polls Forge App REST API (or periodic REST sync)
4. **Enrichment:** Fetch canonical issue state via Jira REST v3
5. **Indexing:** Denormalize into local vector DB or search index
6. **Serving:** Agent queries local index; writes via Jira REST with confirmation

**Strengths:**
- Zero inbound corporate exposure
- Event-driven awareness
- Local autonomy
- Periodic reconciliation possible

---

### Architecture 2: Jira-Only with AWS Integration

**Scenario:** Enterprise Jira + AWS infrastructure already approved

```
Jira
  ↓
Automation trigger
  ↓
Send to SNS topic
  ↓
SNS → SQS
  ↓ (with DLQ)
  ↑
Private EC2 / Lambda worker
  ├─ polls SQS
  ├─ fetches Jira REST for current state
  ├─ processes / enriches
  └─ stores in DynamoDB / RDS
```

**Event types supported:** All Jira webhooks + Automation filtering  
**Durability:** SQS queue guarantees + DLQ fallback  
**Throughput:** Worker independent of Jira event rate  
**Cost:** AWS queue charges; Automation execution quota

**Best when:**
- AWS is approved infrastructure
- Jira is the primary data source
- Enterprise Jira (Automation available)
- Durable buffering is critical

---

### Architecture 3: JSM → On-Premises Script Execution

**Scenario:** Jira Service Management Premium/Enterprise; need to run local scripts

```
JSM event (ticket opened, status changed, etc.)
  ↓
Automation rule
  ↓
"Run script using Jira Edge Connector" action
  ↓
JEC polling queue
  ↑
JEC daemon (installed on-prem)
  ├─ Python / PowerShell / Shell script
  ├─ local filesystem access
  └─ can read/write locally
```

**Strengths:**
- Atlassian-native no-public-URL solution
- Multiple scripting languages supported
- Local script has full LAN access
- Automation-driven triggers

**Limitation:** JSM Premium+ only

---

### Architecture 4: Conservative Private Polling (Zero Apps)

**Scenario:** Minimal Atlassian footprint; direct REST polling acceptable

```
┌──────────────────────────────────────┐
│  Private Worker                      │
├──────────────────────────────────────┤
│                                      │
│  Loop (every 2–5 minutes):           │
│   1. POST /rest/api/3/search/jql     │
│      query: "updated >= {checkpoint}"│
│      fields: [summary, status, ...]  │
│                                      │
│   2. GET /wiki/rest/api/search       │
│      CQL: "lastmodified >= ..."      │
│      then GET /wiki/api/v2/pages/{id}│
│                                      │
│   3. Optional: GET /events-stream    │
│      audit/security events            │
│                                      │
│   4. Deduplicate + process           │
│   5. Save checkpoint                 │
│   6. Sleep N seconds                 │
│                                      │
└──────────────────────────────────────┘
        ↑
        │ outbound HTTPS only
        │
   Atlassian Cloud
```

**Advantages:**
- No external apps to manage
- Simple checkpoint model
- REST API is stable
- Easy to test/debug

**Tradeoff:** Latency = polling interval (2–10 min typical)

---

## Part 4: Decision Matrix for Integration Pattern

### Question 1: Where must event processing happen?

| Answer | Pattern | Architecture |
|---|---|---|
| **Atlassian-hosted (Forge)** | Product Triggers + Async | 1, Forge-based |
| **AWS infrastructure** | Automation → SNS/SQS | 2, AWS queue |
| **On-premises LAN** | JEC (JSM only) | 3, JEC script |
| **Private network (generic)** | Forge inbox or REST polling | 1 or 4 |
| **Acceptable public URL** | Direct webhook | Direct webhook |

---

### Question 2: Do you need low latency (<1 min)?

| Answer | Best Choice | Fallback |
|---|---|---|
| **Yes + public URL ok** | Jira webhook | None |
| **Yes + private network** | Forge trigger | REST polling w/ small interval |
| **No** | REST polling | Forge (async) |

---

### Question 3: Which products matter?

| Products | Recommended |
|---|---|
| **Jira only** | Webhooks + REST, or Automation → SNS |
| **Confluence only** | REST/CQL polling + Forge |
| **Jira + Confluence** | Forge triggers + App REST API |
| **JSM + LAN scripts** | Jira Edge Connector |

---

## Part 5: Complete Request Flow Example

### Use Case: "Sync recent Jira tickets to a private vector DB"

**Setup:**
- Private worker in corporate network
- Jira Cloud
- Local vector database (Weaviate, Pinecone, or Chroma)

**Step-by-step:**

```
1. INITIALIZATION
   └─ Create local checkpoint table
      CREATE TABLE jira_sync_state (
          last_sync TIMESTAMP,
          last_issue_key TEXT,
          last_update_time TIMESTAMP
      );

2. LOOP (every 5 minutes)
   ├─ Read checkpoint
   │  last_time = SELECT last_update_time FROM jira_sync_state;
   │
   ├─ FETCH CANDIDATES (Jira REST JQL search)
   │  POST /rest/api/3/search/jql
   │  {
   │    "jql": "updated >= '{last_time - 5min}' ORDER BY updated ASC",
   │    "fields": ["summary", "description", "status", "assignee"],
   │    "maxResults": 100
   │  }
   │  Use nextPageToken for pagination
   │
   ├─ FOR EACH ISSUE in results:
   │  ├─ Check dedup cache (issue_key + version)
   │  │  SELECT * FROM synced_issues WHERE key='{key}' AND updated='{time}'
   │  │
   │  ├─ If NOT seen:
   │  │  ├─ Generate embedding
   │  │  │  embedding = local_model.encode(summary + " " + description)
   │  │  │
   │  │  ├─ Write to vector DB
   │  │  │  db.upsert(
   │  │  │    id="{key}",
   │  │  │    vector=embedding,
   │  │  │    metadata={
   │  │  │      "jira_key": key,
   │  │  │      "summary": summary,
   │  │  │      "status": status,
   │  │  │      "last_updated": updated_time,
   │  │  │      "source_url": issue_url
   │  │  │    }
   │  │  │  )
   │  │  │
   │  │  └─ Mark as synced
   │  │     INSERT INTO synced_issues (key, updated, synced_at)
   │  │     VALUES ('{key}', '{time}', NOW())
   │  │
   │  └─ Update checkpoint
   │     UPDATE jira_sync_state 
   │     SET last_update_time = '{issue.updated}'
   │
   └─ Sleep 5 minutes
```

**Canonical state fetch (when needed):**
```
When user asks: "What's the status of MOBILE-456?"
  ├─ GET /rest/api/3/issue/MOBILE-456
  │  ?fields=summary,status,assignee,updated,priority,description
  │
  ├─ Parse Atlassian Document Format (ADF) for description
  │
  └─ Return fresh authoritative state (not from vector DB)
```

**Periodic reconciliation (nightly):**
```
midnight_job():
  ├─ Fetch ALL issues modified in last 30 days
  │  JQL: "updated >= -30d"
  │
  ├─ Compare with vector DB IDs
  │
  ├─ Reindex any missing or stale
  │
  └─ Log any discrepancies
```

---

## Part 6: Complete Response Flow Example

### Use Case: "AI agent suggests a Jira ticket resolution"

**Flow:**

```
USER QUERY
└─ "What should we do about the unassigned mobile bugs?"

AGENT SEARCH (local + fast)
├─ Query vector DB
│  search(
│    query="mobile bugs unassigned",
│    filters={status: "Open"},
│    limit=10
│  )
│
└─ Return: [MOBILE-123, MOBILE-456, MOBILE-789]

AGENT ENRICHMENT (REST canonical fetch)
├─ GET /rest/api/3/issue/MOBILE-123
│  └─ Full context: watchers, history, linked issues
│
├─ GET /rest/api/3/issue/MOBILE-456
│  └─ Fetch only if needed for reasoning
│
└─ Skip others for efficiency

AGENT REASONING
├─ Synthesize from:
│  ├─ Vector DB metadata (quick facts)
│  ├─ Fresh REST payload (authoritative)
│  ├─ LLM context window
│  └─ Domain knowledge
│
└─ Generate recommendation

PROPOSED ACTION
├─ "Assign MOBILE-123 to @alice (2 similar issues she handled)"
│
└─ CONFIRM WITH USER

WRITE-BACK (with confirmation)
├─ PUT /rest/api/3/issue/MOBILE-123
│  {
│    "fields": {
│      "assignee": {"accountId": "alice_id"}
│    }
│  }
│
└─ POST /rest/api/3/issue/MOBILE-123/comment
   {
     "body": {
       "version": 1,
       "type": "doc",
       "content": [
         {
           "type": "paragraph",
           "content": [
             {
               "type": "text",
               "text": "Assigned per AI suggestion based on related issue pattern"
             }
           ]
         }
       ]
     }
   }

FEEDBACK LOOP
└─ Log action
   ├─ Issue key
   ├─ Action taken
   ├─ AI confidence
   └─ User feedback (for model tuning)
```

---

## Part 7: Security & Best Practices

### API Credential Management

| Method | Security | Use Case |
|---|---|---|
| **API Token + Email** | Medium | Internal scripts, one-off use |
| **OAuth 2.0 (3LO)** | High | Distributed apps; user context |
| **Service Account API Key** | High | Dedicated service principals |
| **Forge App Secrets** | High | Atlassian-managed; no agent exposure |

**Rules:**
1. Never log credentials or full payloads
2. Rotate API tokens every 90 days
3. Use least-privilege scopes
4. For Forge: use `ignoreSelf` and trigger filters

---

### Event Processing Safety

```
webhook received
  ↓
✓ Validate signature / auth header
  ↓
✓ Deduplicate by event ID
  ↓
✓ Enqueue immediately (return HTTP 2xx quickly)
  ↓
✓ Async worker:
  ├─ Fetch canonical state via REST
  ├─ Check version/timestamp
  ├─ Decide to process or skip
  └─ Update local state atomically
  ↓
✓ Monitor failed deliveries
  ↓
✓ Periodic reconciliation (catch missed events)
```

---

### Rate Limiting & Quota

**Jira:** Points-based quota; monitor `RateLimit-*` headers  
**Confluence:** Points per request; obey `Retry-After`  
**Retry strategy:**
```
for attempt in range(MAX_RETRIES):
    response = call_atlassian()
    if response.status != 429:
        return response
    wait = int(response.headers.get("Retry-After", 2 ** attempt))
    sleep(wait + random_jitter())
```

---

## Part 8: Decision Tree

Start here to choose your architecture:

```
Do you have a public, routable HTTPS endpoint?
│
├─ YES → Direct Jira webhook is simplest
│        └─ (add Confluence via Forge triggers if needed)
│
└─ NO → No public endpoint allowed
         │
         ├─ JSM Premium/Enterprise?
         │  └─ YES → Test Jira Edge Connector first
         │           (if unsatisfactory, fall through)
         │
         ├─ Need Jira + Confluence together?
         │  └─ YES → Forge product triggers + event inbox
         │           OR: private REST polling (simpler)
         │
         ├─ AWS infrastructure approved?
         │  └─ YES → Automation → SNS → SQS
         │           (Jira-only, but very durable)
         │
         └─ Generic private network?
            └─ REST polling (JQL/CQL with checkpoints)
               (conservative, simple, proven)
```

---

## Part 9: Implementation Checklist

### Phase 1: Foundation

- [ ] Choose event pattern (webhook / Forge / polling / queue)
- [ ] Choose REST API layer (direct REST / MCP / ACLI)
- [ ] Set up authentication (API token / OAuth / Forge secrets)
- [ ] Create local state tables (checkpoint, dedup, processed events)
- [ ] Implement retry logic with exponential backoff + jitter

### Phase 2: Event Collection

- [ ] Register event source (webhook / Forge trigger / polling loop)
- [ ] Implement deduplication (event ID tracking)
- [ ] Add monitoring / alerting for failures
- [ ] Test event payload parsing
- [ ] Implement fast enqueue (don't block on processing)

### Phase 3: Data Enrichment

- [ ] Fetch canonical state via REST (don't trust event payload alone)
- [ ] Handle permission-denied (some fields visible only to certain roles)
- [ ] Parse rich text (Atlassian Document Format for Jira; storage format for Confluence)
- [ ] Extract attachments / references on demand only
- [ ] Cache frequently accessed objects (projects, users, workflows)

### Phase 4: Local Processing

- [ ] Decide: database vs. index vs. vector embeddings
- [ ] Denormalize for local queries (de-ID users for privacy if needed)
- [ ] Implement idempotent updates (handle duplicates gracefully)
- [ ] Test version conflicts (what if REST payload differs from event?)
- [ ] Add logging for audit trail

### Phase 5: Write-Back (if applicable)

- [ ] Implement confirmation workflow (require human approval for writes)
- [ ] Use Jira REST for mutations (search for object, then update)
- [ ] Add audit comment to explain changes
- [ ] Handle permission errors (graceful failure)
- [ ] Log all mutations

### Phase 6: Reconciliation

- [ ] Implement periodic full refresh (daily or weekly)
- [ ] Compare local state with REST truth
- [ ] Detect and reindex stale/missing objects
- [ ] Monitor lag metrics
- [ ] Alert on reconciliation failures

---

## Part 10: Recommended Stack Summary

### For a custom private AI agent (Jira + Confluence):

```
primary:     Forge product triggers + event inbox
fallback:    REST JQL/CQL polling (simpler, no app)
write-back:  Jira/Confluence REST + confirmation UI
storage:     Local PostgreSQL / vector DB
model:       Claude API with function calling
monitoring:  CloudWatch / DataDog / custom logs
```

### For JSM → internal automation:

```
primary:     Jira Edge Connector
action:      Python / PowerShell scripts
storage:     None needed (direct LAN access)
```

### For Jira in AWS:

```
primary:     Automation → SNS → SQS
consumer:    Lambda / EC2 worker
storage:     DynamoDB / RDS
fallback:    Jira REST polling
```

---

## Conclusion

The two research documents address complementary halves of integration:

| Aspect | From Research 1 | From Research 2 |
|---|---|---|
| **How to read data** | REST / MCP / APIs | Fetch after event |
| **How to know change happened** | Polling optional | Webhooks / Forge / polling |
| **Architecture patterns** | Individual operations | End-to-end flows |

**Central insight:** Always fetch canonical state via REST after receiving an event signal. Never trust the event payload alone as your system-of-record.

**Central rule:** If exposing a public endpoint is forbidden, Forge triggers or REST polling are your strongest tools. Jira Edge Connector works for JSM. AWS queue bridges work for Jira-only in AWS environments.

This combined research enables you to design integrations that are:
- **Reliable** (deduplicated, reconciled, checksummed)
- **Secure** (private network, minimal credential exposure)
- **Scalable** (async processing, checkpoint-based sync)
- **Auditable** (logged mutations, clear data lineage)

---

**End of Combined Research Flow**
