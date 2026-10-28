# Atlassian Jira & Confluence: Webhooks, Event Streams, Subscriptions, and Private-Network Delivery

**Research date:** 1 October 2026  
**Primary scope:** Atlassian Cloud — Jira, Jira Software, Jira Service Management, Confluence, Forge, organization audit events, Automation.  
**Special focus:** ways users or software can be notified of events, especially approaches that **do not require a publicly accessible URL on your own infrastructure**.

---

## 1. Executive findings

Atlassian has several separate event systems rather than one universal event-stream API:

| Mechanism | Model | Jira | Confluence | Public receiver on your infrastructure? | Best use |
|---|---|---:|---:|---:|---|
| Jira Cloud webhooks | HTTPS push | Yes | No equivalent general Cloud REST webhook API | **Yes** | Low-latency integrations |
| Forge product triggers | Atlassian-hosted async event invocation | Yes | Yes | **No** | App/agent event processing |
| Forge Dynamic Triggers | Runtime subscription registration | Yes | Yes | **No** | User-configurable subscriptions |
| Organization `/events-stream` | Cursor-based HTTP polling | Audit events | Audit events | **No** | Security/audit collection |
| Organization audit webhook | HTTPS push | Org/audit | Org/audit | Yes, unless using third-party receiver | SIEM |
| Jira/Confluence Automation | Trigger → action | Yes | Yes | Depends on action | Low-code routing |
| Jira Edge Connector (JEC) | Private poller/queue → local script | JSM | No generic Confluence equivalent | **No** | Run code inside LAN |
| Jira Automation → AWS SNS/SQS | Managed queue | Yes | Not documented as equivalent Confluence action | **No** | Durable private consumption |
| REST JQL/CQL polling | Pull | Yes | Yes | **No** | General private sync |
| RSS | Pull | Yes | Yes | **No** | Lightweight feeds |
| Email / watchers | Human notification | Yes | Yes | **No** | User notifications |
| Slack / Microsoft Teams | Provider-hosted notifications | Yes | Yes/varies by integration | **No internal endpoint** | Human/team notifications |
| Forge Realtime | WebSocket for Forge frontends | Via Forge | Via Forge | **No** | Live Forge UI |
| MCP v2 | Request/response tools | Yes | Yes | N/A | Agent actions, **not** product-event subscription |

### Core networking fact

A normal webhook is an outbound HTTPS callback from Atlassian. Atlassian must be able to route to the callback address. A receiver available only at `10.x.x.x`, `192.168.x.x`, `127.0.0.1`, or an internal-only DNS name cannot be a direct Jira Cloud webhook target.

If your requirement is **“nothing in our corporate network may accept inbound Internet traffic”**, you have strong alternatives:

1. **Forge trigger → process entirely inside Forge**
2. **Forge trigger → Forge queue/storage → private system pulls**
3. **Jira Edge Connector (JEC)** for JSM Premium/Enterprise
4. **Jira Automation → Amazon SNS → SQS → internal consumer polls**
5. **Organization `/events-stream` polling** for audit/security events
6. **Jira JQL + Confluence CQL polling** for general domain changes
7. RSS/email/Slack/Teams for human-facing notification

A reverse tunnel avoids opening an inbound firewall port on the origin, but it still creates a publicly reachable URL. It is therefore **not** a strict “no public URL” solution.

---

# 2. What “event stream” means in Atlassian

Do not conflate these mechanisms.

### Webhook

```text
Jira
  |
  | HTTPS POST
  v
https://receiver.example.com/webhook
```

A routable receiver is required.

### Polling event feed

```text
Private worker
    |
    | outbound HTTPS GET
    v
Atlassian /events-stream
```

No inbound endpoint is required.

### Forge product trigger

```text
Jira / Confluence
       |
       v
Atlassian event infrastructure
       |
       v
Forge function hosted by Atlassian
```

No customer callback URL is required.

### Queue bridge

```text
Jira Automation
      |
      v
Amazon SNS
      |
      v
Amazon SQS
      ^
      |
Private worker polls
```

Again, no inbound listener on the private worker.

---

# 3. Jira Cloud webhooks

Official docs:

- Webhooks guide: https://developer.atlassian.com/cloud/jira/platform/webhooks/
- REST v3 webhook API: https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-webhooks/

Atlassian defines a Jira webhook as a user-defined callback over HTTPS.

## 3.1 Receiver requirements

Current Jira Cloud documentation states that:

- the callback must use secure HTTPS
- the server certificate must be signed by a globally trusted CA
- Atlassian documents supported callback ports for admin webhooks
- outgoing Atlassian IP ranges may need to be allow-listed
- redirects are limited

Therefore, this does **not** work directly:

```text
Atlassian Cloud
     |
     X
     |
10.10.20.50:9000
```

Firewall allowlisting alone cannot make a private RFC1918 address routable from Atlassian Cloud.

---

# 4. Ways to register Jira webhooks

Atlassian currently documents these registration methods.

## 4.1 Jira Administration UI

An administrator can configure a webhook from Jira settings.

Good for:

- manually managed integrations
- simple fixed callback
- administrator-controlled event selection

## 4.2 Admin webhook REST API

Admin/“other integrations” webhook family:

```http
/rest/webhooks/1.0/webhook
```

This is separate from the OAuth/Connect dynamic v3 API below.

## 4.3 Dynamic webhook REST API

Current Jira v3 operations include:

```http
GET    /rest/api/3/webhook
POST   /rest/api/3/webhook
DELETE /rest/api/3/webhook
PUT    /rest/api/3/webhook/refresh
GET    /rest/api/3/webhook/failed
```

These dynamic operations are intended for applicable OAuth 2.0 / app scenarios and carry restrictions.

## 4.4 Atlassian Connect webhook module

Connect historically supported descriptor-defined webhooks.

However, Atlassian has ended new Connect Marketplace publishing and states that new extensibility capabilities are delivered on Forge. Treat Connect as migration/legacy context, not the preferred foundation for a new event architecture.

---

# 5. Jira webhook event catalog

The current general Jira webhook guide lists these event families.

## Issues/work items

```text
jira:issue_created
jira:issue_updated
jira:issue_deleted
```

JQL filtering supported.

## Issue properties

```text
issue_property_set
issue_property_deleted
```

## Worklogs

```text
worklog_created
worklog_updated
worklog_deleted
```

## Comments

```text
comment_created
comment_updated
comment_deleted
```

## Attachments

```text
attachment_created
attachment_deleted
```

## Issue links

```text
issuelink_created
issuelink_deleted
```

## Issue types

```text
issuetype_created
issuetype_updated
issuetype_deleted
```

## Projects

```text
project_created
project_updated
project_deleted
project_soft_deleted
project_restored_deleted
project_archived
project_restored_archived
```

## Versions

Current webhook documentation contains creation/update/move/release/unrelease/delete/merge-related version events.

## Filters

```text
filter_created
filter_updated
filter_deleted
```

## Users

```text
user_created
user_updated
user_deleted
```

## System configuration

Atlassian documents webhook events for changes to options such as voting, watching, unassigned issues, subtasks, issue links, and time tracking.

## Sprints

```text
sprint_created
sprint_updated
sprint_started
sprint_closed
sprint_deleted
```

## Boards

```text
board_created
board_updated
board_deleted
board_configuration_changed
```

## Jira expressions

```text
jira_expression_evaluation_failed
```

The authoritative live list is:
https://developer.atlassian.com/cloud/jira/platform/webhooks/#available-webhook-events

---

# 6. Jira dynamic webhook REST API

## 6.1 Registration

```http
POST /rest/api/3/webhook
```

Example:

```json
{
  "url": "https://receiver.example.com/jira",
  "webhooks": [
    {
      "jqlFilter": "project = MOBILE AND status != Done",
      "events": [
        "jira:issue_created",
        "jira:issue_updated"
      ]
    }
  ]
}
```

## 6.2 Dynamic API event subset

The dynamic registration endpoint supports only a subset of all Jira webhook events. The current documented set includes:

```text
jira:issue_created
jira:issue_updated
jira:issue_deleted

comment_created
comment_updated
comment_deleted

issue_property_set
issue_property_deleted

sprint_created
sprint_updated
sprint_closed
sprint_deleted
sprint_started

jira:version_released
jira:version_unreleased
jira:version_created
jira:version_moved
jira:version_updated
jira:version_merged
jira:version_deleted
```

Do not assume every general webhook event can be registered dynamically.

## 6.3 JQL restrictions

The dynamic API supports a restricted JQL subset for event matching.

Current documentation lists fields/clauses including:

```text
issueKey
project
issuetype
status
priority
assignee
reporter
issue.property
cf[id]  (restricted custom-field support)
```

Supported operators include:

```text
=
!=
IN
NOT IN
```

Sprint and version webhooks are special: their required `jqlFilter` is not used to restrict those event types. If you only want some sprint/version events, perform filtering in your consumer.

## 6.4 Registration limits

Current documented limits:

- Connect: maximum 100 webhooks per app per tenant
- OAuth 2.0: maximum 5 webhooks per app per user per tenant

## 6.5 Expiration

Dynamic registrations expire after **30 days** unless refreshed.

Use:

```http
PUT /rest/api/3/webhook/refresh
```

to extend their life rather than assuming a registration is permanent.

---

# 7. Jira webhook delivery semantics

## 7.1 Retries

Jira currently documents up to **five retries** after callback failure.

Retryable conditions include:

```text
408
409
425
429
5xx
connection failure
timeout
```

Backoff is randomized; Atlassian currently documents retry attempts occurring approximately 5–15 minutes after the previous failed attempt.

## 7.2 Duplicate events

Duplicate delivery is possible.

Jira provides:

```text
X-Atlassian-Webhook-Identifier
```

The identifier is stable across retries and should be used for deduplication.

Retrials include:

```text
X-Atlassian-Webhook-Retry
```

## 7.3 Primary and Secondary flows

Header:

```text
X-Atlassian-Webhook-Flow
```

Values:

```text
Primary
Secondary
```

Current Jira documentation states approximate delivery expectations of:

- Primary: within ~30 seconds
- Secondary/bulk/cascade: up to ~15 minutes

## 7.4 Failed-webhook API

Failed webhooks can be inspected via the Jira webhook REST API:

```http
GET /rest/api/3/webhook/failed
```

## 7.5 Consumer architecture

Avoid expensive synchronous work:

```text
webhook POST
   |
   +--> validate
   +--> dedup
   +--> enqueue
   +--> HTTP 2xx quickly

worker
   |
   +--> Jira REST canonical fetch
   +--> process/index
```

---

# 8. Forge product-event triggers

Official docs:

- Events overview: https://developer.atlassian.com/platform/forge/events-reference/
- Trigger module: https://developer.atlassian.com/platform/forge/manifest-reference/modules/trigger/
- Jira: https://developer.atlassian.com/platform/forge/events-reference/jira/
- Jira Software: https://developer.atlassian.com/platform/forge/events-reference/jira-software/
- Jira Service Management: https://developer.atlassian.com/platform/forge/events-reference/jira-service-management/
- Confluence: https://developer.atlassian.com/platform/forge/events-reference/confluence/

Forge is the most important Atlassian-native answer to a **no-public-callback** requirement.

## 8.1 Flow

```text
Jira/Confluence action
        |
        v
Atlassian event infrastructure
        |
        v
Forge trigger
        |
        v
your Forge function
```

The function executes on Atlassian infrastructure.

No endpoint needs to be exposed from your network.

## 8.2 Static trigger example

```yaml
modules:
  trigger:
    - key: jira-change-trigger
      function: jira-event-handler
      events:
        - avi:jira:created:issue
        - avi:jira:updated:issue
        - avi:jira:deleted:issue
      filter:
        ignoreSelf: true

  function:
    - key: jira-event-handler
      handler: src/events.handleJiraEvent
```

Handler:

```javascript
export async function handleJiraEvent(event, context) {
  console.log(event.eventType);
  console.log(event.issue?.key);
}
```

## 8.3 Filtering

Trigger filters support capabilities including:

```text
ignoreSelf
expression
appIsLicensed
onError
```

Example:

```yaml
filter:
  ignoreSelf: true
  expression: event.issue.fields?.issueType.name == 'Bug'
  onError: RECEIVE_AND_LOG
```

Filtering reduces unnecessary invocations.

## 8.4 Latency

Forge trigger events are queued asynchronously.

Current Atlassian documentation states that delivery to a trigger can take **up to 3 minutes** after the product action.

Therefore Forge is excellent for asynchronous integration, but it is not a hard sub-second event bus.

---

# 9. Forge Jira event coverage

Current Forge Jira event references cover a broad set including:

- issue created/updated/deleted
- assigned
- viewed
- mentioned
- issue links
- issue types
- custom-field contexts
- projects
- versions
- attachments
- components
- users
- filters
- worklogs
- additional product/platform objects

Jira Software events include board and sprint events such as:

```text
board created/updated/deleted/configuration changed
sprint created/started/updated/closed/deleted
```

For exact event names, scopes, and payloads use:

- https://developer.atlassian.com/platform/forge/events-reference/jira/
- https://developer.atlassian.com/platform/forge/events-reference/jira-software/

---

# 10. Forge Confluence event coverage

Current Confluence Forge event categories include:

- pages
- live docs
- blog posts
- whiteboards
- databases
- smart links
- folders
- inline tasks
- comments
- spaces
- attachments
- custom content
- labels
- users
- groups
- relations
- searches

Representative names include:

```text
avi:confluence:created:page
avi:confluence:updated:page
avi:confluence:deleted:page
avi:confluence:viewed:page
avi:confluence:moved:page
avi:confluence:copied:page

avi:confluence:created:custom_content
avi:confluence:updated:custom_content
avi:confluence:deleted:custom_content

avi:confluence:added:label
avi:confluence:removed:label

avi:confluence:created:user
avi:confluence:deleted:user

avi:confluence:created:group
avi:confluence:deleted:group

avi:confluence:performed:search
```

Authoritative catalog:
https://developer.atlassian.com/platform/forge/events-reference/confluence/

---

# 11. Cascading-event warning

A critical sync consideration: Forge documentation notes that cascading deletion events are not necessarily emitted for every child entity.

Do not assume:

```text
delete parent
  == one delete event for every descendant
```

Instead:

```text
parent delete event
      |
      v
invalidate parent + known descendants
      |
      v
REST/reconciliation if necessary
```

This is especially important for search indexes and local mirrors.

---

# 12. Forge Dynamic Triggers

Official docs:

- Dynamic Modules: https://developer.atlassian.com/platform/forge/apis-reference/dynamic-modules/
- Dynamic Modules API: https://developer.atlassian.com/platform/forge/apis-reference/dynamic-modules-api/
- Trigger module: https://developer.atlassian.com/platform/forge/manifest-reference/modules/trigger/

**Current status: Preview.**

Dynamic modules let a Forge app register different modules per installation at runtime.

For event subscriptions, Jira and Confluence support dynamic **Trigger** modules.

## 12.1 API family

```http
POST   /forge/installation/v2/dynamic/module
GET    /forge/installation/v2/dynamic/module
PUT    /forge/installation/v2/dynamic/module/{key}
DELETE /forge/installation/v2/dynamic/module/{key}
```

Use the live reference for exact schemas/trailing-slash behavior.

## 12.2 Example

```javascript
import { asApp, route } from "@forge/api";

const payload = {
  type: "trigger",
  data: {
    events: ["avi:jira:updated:issue"],
    function: "issue-handler"
  }
};

const response = await asApp().requestAtlassian(
  route`/forge/installation/v2/dynamic/module`,
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  }
);
```

Current documentation states:

- maximum 100 dynamic modules per app installation
- remote backends can perform dynamic-module operations only as app, not as user

This is a strong replacement direction for many dynamic Connect webhook use cases.

---

# 13. Forge Async Events API

Official docs:
https://developer.atlassian.com/platform/forge/runtime-reference/async-events-api/

This is not the initial Jira/Confluence subscription mechanism. It is an internal queue for work after your Forge trigger receives an event.

Pattern:

```text
product event
   |
   v
Forge trigger
   |
   v
Forge Async Queue
   |
   v
consumer
```

Example:

```javascript
import { Queue } from "@forge/events";

const queue = new Queue({ key: "product-events" });

export async function onProductEvent(event) {
  await queue.push({
    body: {
      type: event.eventType,
      issueKey: event.issue?.key,
      contentId: event.content?.id
    }
  });
}
```

Consumer manifest:

```yaml
modules:
  consumer:
    - key: product-event-consumer
      queue: product-events
      function: worker

  function:
    - key: worker
      handler: src/consumer.handler
      timeoutSeconds: 600
```

Current documented characteristics include:

- maximum 50 events per `push`
- up to 200 KB combined push payload
- delayed processing
- async consumers can have extended function runtime, currently up to 15 minutes

Always verify current quota docs before production sizing.

---

# 14. Forge Storage as event inbox

A useful private integration pattern is to persist compact event records inside Forge:

```json
{
  "source": "jira",
  "eventType": "avi:jira:updated:issue",
  "entity": "MOBILE-123",
  "receivedAt": "2026-10-01T17:10:00Z",
  "processed": false
}
```

The event payload is best treated as a **change signal**. Fetch canonical current state with Jira/Confluence REST when processing.

---

# 15. Forge App REST API: event inbox → private puller

Official docs:

- https://developer.atlassian.com/platform/forge/app-rest-apis/
- https://developer.atlassian.com/platform/forge/expose-forge-app-rest-apis/
- https://developer.atlassian.com/platform/forge/access-rest-apis-exposed-by-a-forge-app/

**Current status: Preview.**

Currently documented for Jira and Confluence Forge apps.

This gives a compelling architecture:

```text
Jira / Confluence
       |
       v
Forge trigger
       |
       v
Forge Storage / queue
       ^
       |
       | outbound HTTPS GET
       |
private corporate collector
```

No public callback exists on the corporate network.

Forge App REST API URLs are Atlassian-hosted. Current documented patterns include:

```text
https://api.atlassian.com/svc/{product}/{cloud-id}/apps/{app-id}_{env-id}/{path}
```

and the site gateway equivalent.

Security uses:

- OAuth 2.0 / 3LO
- app-defined scopes
- site/admin enablement
- Atlassian-hosted execution

App REST APIs are disabled by default for a site and an admin must enable them.

A useful event inbox API design:

```http
GET  /events?cursor=...
POST /events/{id}/ack
GET  /health
```

Private worker:

```text
loop:
    GET Atlassian-hosted /events
    process
    checkpoint
    repeat
```

This provides event-driven capture with pull-based private delivery.

**Caveat:** the feature is Preview.

---

# 16. Forge Realtime

Official docs:

- https://developer.atlassian.com/platform/forge/realtime/
- https://developer.atlassian.com/platform/forge/runtime-reference/realtime-events-api/
- https://developer.atlassian.com/platform/forge/apis-reference/ui-api-bridge/realtime/

Forge Realtime uses WebSockets.

It allows Forge frontends to subscribe to realtime channels and Forge backend code to publish events.

Pattern:

```text
Jira/Confluence event
        |
        v
Forge trigger/backend
        |
        v
Realtime publish
        |
        v
open Forge UI instances
```

Useful for:

- live counters
- activity views
- collaborative Forge UI
- push updates while a user has the app open

It is **not a general-purpose external Jira/Confluence WebSocket feed for arbitrary backend daemons**.

Also be careful with global channels. Atlassian warns that global channels are not automatically constrained by the same content context as scoped channels; authorization must be designed carefully.

---

# 17. Forge Remote

A Forge trigger can invoke a remote backend instead of a Forge function.

That flow is:

```text
Atlassian product event
        |
        v
Forge trigger
        |
        | HTTPS
        v
remote backend
```

This still requires the remote backend to be reachable.

So Forge Remote **does not solve** a strict no-public-ingress requirement.

Choose it when you explicitly want externally hosted compute and have an approved public ingress architecture.

---

# 18. Organization audit Events API

Official reference:
https://developer.atlassian.com/cloud/admin/organization/rest/api-group-events/

This is distinct from Jira issue/page events.

Important endpoints include:

```http
GET /v1/orgs/{orgId}/events
GET /v1/orgs/{orgId}/events/{eventId}
GET /v1/orgs/{orgId}/event-actions
GET /v1/orgs/{orgId}/events-stream
```

The Events API is for organization/audit activity.

---

# 19. `/events-stream`: purpose-built polling feed

Endpoint:

```http
GET https://api.atlassian.com/admin/v1/orgs/{orgId}/events-stream
```

Example:

```bash
curl \
  "https://api.atlassian.com/admin/v1/orgs/$ORG_ID/events-stream?limit=500&sortOrder=asc" \
  -H "Authorization: Bearer $ORG_API_KEY" \
  -H "Accept: application/json"
```

Despite its name, this is **not** a WebSocket or SSE connection.

It is a paginated polling API.

## Parameters

Current docs describe:

```text
cursor
from
to
limit
sortOrder
```

Atlassian's migration guide states:

- default page size: 200
- maximum page size: 500
- cursor pagination
- a cursor is returned even on the last page to support continued polling

## Processing-time ordering

A particularly useful property: the polling API sorts by event **processing time**, not only by the time the activity originally occurred.

Responses expose:

```text
processedAt
```

This reduces the chance that an event processed late is skipped by a naïve occurrence-time cursor.

## Example loop

```python
cursor = load_checkpoint()

while True:
    response = get_events_stream(
        cursor=cursor,
        limit=500,
        sort_order="asc"
    )

    for event in response["data"]:
        if not seen(event["id"]):
            process(event)
            mark_seen(event["id"])

    cursor = response["meta"]["next"]
    save_checkpoint(cursor)
    sleep(POLL_SECONDS)
```

Use the live response's cursor/link semantics exactly.

## Advantages

- zero inbound connectivity
- only outbound HTTPS
- cursor model
- event IDs
- audit/security context

## Limitation

This is an **audit-log** feed.

It is not equivalent to the complete Jira and Confluence product-domain event catalogs.

Audit availability can depend on:

- subscription/entitlement
- event category
- audit settings
- event exclusions
- product activity coverage

Some audit activity can also take several minutes to appear.

---

# 20. Organization audit-log webhooks

Official docs:
https://support.atlassian.com/security-and-access-policies/docs/learn-more-about-audit-log-webhooks/

This is push delivery of audit-log activity.

Current documentation states:

- organization-admin capability
- plan/entitlement requirements such as eligible Enterprise products or Atlassian Guard Premium
- up to 3 audit webhooks per organization
- optional Authorization header
- asynchronous delivery
- up to 3 retries
- ordering is not guaranteed
- duplicate delivery is possible
- duplicate copies retain the same event ID
- no public delivery SLO

This is excellent when a SIEM already provides a webhook ingestion URL.

It still requires a reachable destination.

---

# 21. Audit-log exclusions

Official docs:
https://support.atlassian.com/security-and-access-policies/docs/manage-event-exclusions-from-your-audit-log/

If the organization excludes an event category, Atlassian documents that it is no longer:

- stored in the audit log
- available through the audit-log API
- available to audit-log webhooks

Events that occur while excluded cannot later be recovered from the audit store.

That means an audit-event consumer must include audit configuration in its completeness assessment.

---

# 22. Jira Automation as an event router

Official actions:
https://support.atlassian.com/cloud-automation/docs/jira-automation-actions/

Automation can be used as:

```text
Jira trigger
    |
    +--> email
    +--> Slack
    +--> Microsoft Teams
    +--> Twilio SMS
    +--> web request
    +--> Amazon SNS
    +--> AWS SSM
    +--> Jira Edge Connector
    +--> other supported connectors
```

This is important because you can build event routing without creating an Atlassian app.

---

# 23. Jira Automation → Amazon SNS → SQS

Jira Automation currently documents a **Send message to Amazon SNS topic** action.

This enables a very strong private-network architecture:

```text
Jira event
   |
   v
Automation
   |
   v
Amazon SNS
   |
   v
Amazon SQS
   ^
   |
   | long/short poll outbound
   |
private worker
```

Why add SQS?

SNS is primarily fan-out. SQS provides:

- durable pull queue
- worker backpressure
- offline tolerance
- retry handling
- dead-letter queue
- no inbound endpoint on your private worker

Advantages:

- no corporate public listener
- AWS IAM
- queue durability
- scalable
- event payload can be built from Jira Automation smart values

Tradeoffs:

- AWS dependency
- Automation quotas
- Automation-defined payload rather than the raw Jira webhook format
- not documented as an identical Confluence Automation action

---

# 24. Jira Automation → AWS SSM

Jira Automation also documents a **Run AWS SSM document** action.

Pattern:

```text
Jira event
   |
   v
Automation
   |
   v
AWS Systems Manager
   |
   v
managed machine / automation document
```

This can invoke infrastructure without publishing an Atlassian-facing endpoint.

If you use SSM-managed on-prem/hybrid nodes, the machine communicates through AWS's management channel rather than accepting an Atlassian webhook.

Confirm your AWS SSM hybrid-node architecture separately.

---

# 25. Jira Edge Connector (JEC)

Official documentation:

- Overview:
  https://support.atlassian.com/jira-service-management-cloud/docs/jira-edge-connector-jec-an-extensibility-platform/
- Automation integration:
  https://support.atlassian.com/cloud-automation/docs/use-jira-automation-with-jira-edge-connector-jec/
- Install:
  https://support.atlassian.com/jira-service-management-cloud/docs/install-jira-edge-connector/
- Configure:
  https://support.atlassian.com/jira-service-management-cloud/docs/configure-jira-edge-connector/
- Source:
  https://github.com/atlassian/jec

This is one of the most relevant findings for the no-public-URL requirement.

Atlassian explicitly describes JEC as a lightweight application for:

- integrating systems that are **not accessible from the public Internet**
- running executables triggered by Jira Service Management
- deployment on-premises or in the customer's cloud

## Supported script types

Current docs list:

```text
Groovy
Python
Go
PowerShell
Shell
Windows batch
```

## How it avoids inbound Internet

JEC has a poller configuration. Atlassian's JEC configuration documentation describes `pollerConf` as controlling the poller that polls JEC's queue.

Architecture:

```text
JSM event
   |
   v
Automation
   |
   v
Atlassian/JEC queue
   ^
   |
   | outbound polling
   |
JEC inside LAN
   |
   v
local script/executable
```

Instead of:

```text
Atlassian -> your internal HTTP listener
```

the network direction becomes:

```text
your connector -> Atlassian queue
```

## Setup outline

1. Install JEC on an internal Linux/Windows host.
2. Create a JEC integration/connection.
3. Create/copy its API key.
4. Put the key in the JEC config.
5. Define `actionMappings`.
6. Map an action to a local executable/script.
7. Run JEC.
8. Create an Automation flow.
9. Add **Run script using Jira Edge Connector**.
10. Send key/value data to the script.

Conceptual config:

```yaml
appName: InternalJiraWorker
apiKey: "<JEC_API_KEY>"
baseUrl: "https://api.atlassian.com"

actionMappings:
  ProcessEvent:
    sourceType: local
    filepath: "/opt/company/process-event.py"

pollerConf:
  pollingWaitIntervalInMillis: 100
  visibilityTimeoutInSeconds: 30
  maxNumberOfMessages: 10
```

Use the exact live config schema for your installed JEC version.

## Plan limitation

Atlassian currently documents the JEC Automation action as available with:

```text
Jira Service Management Premium and Enterprise
```

Therefore it is not a universal free Jira/Confluence event connector.

---

# 26. Automation → web request

Jira and Confluence Automation can send outgoing web requests.

This is convenient, but fundamentally webhook-like:

```text
Automation
   |
   | HTTP
   v
your endpoint
```

If the destination is private-only, Atlassian cannot call it directly.

A public relay/tunnel/provider is still required unless you choose another action such as JEC/SNS/SSM.

---

# 27. Confluence Automation notifications

Official docs:
https://support.atlassian.com/cloud-automation/docs/actions-in-confluence-automation/

Current Confluence Automation notification actions include:

- send email
- send Microsoft Teams message
- send Slack message
- send Twilio SMS
- send web request

Slack/Teams/Twilio endpoints are provider-hosted, so your own corporate server is not exposed.

`Send web request` still requires a reachable destination.

---

# 28. Human notification: Jira native email

Official docs:

- Notification schemes:
  https://support.atlassian.com/jira-cloud-administration/docs/configure-notification-schemes/
- Email notifications:
  https://support.atlassian.com/jira-cloud-administration/docs/configure-email-notifications/
- Custom workflow events:
  https://support.atlassian.com/jira-cloud-administration/docs/configure-email-notifications-for-a-custom-event/

Jira notification schemes map events to recipients such as:

- assignee
- reporter
- current user
- project/space owner
- component lead
- individual user
- group
- project/space role
- watchers
- user-picker custom field
- group-picker custom field

Custom workflow events can also fire email notifications.

This requires no custom server.

---

# 29. Human notification: Confluence watches/email

Official docs:

- https://support.atlassian.com/confluence-cloud/docs/watch-pages-spaces-and-blogs/
- https://support.atlassian.com/confluence-cloud/docs/subscribe-to-email-notifications/

Users can watch supported content and spaces.

Notifications can cover edits, comments, attachments, deletion and other activity depending on content type/settings.

Confluence supports immediate notifications for some time-sensitive activity plus digest behavior.

No custom server is required.

---

# 30. Jira saved-filter subscriptions

Official docs:
https://support.atlassian.com/jira-software-cloud/docs/save-your-search-as-a-filter/

Saved JQL filters can be:

- emailed on a schedule
- shared
- exported
- exposed as RSS

This gives a zero-inbound scheduled notification mechanism.

Example:

```jql
project = MOBILE
AND updated >= -1h
ORDER BY updated DESC
```

It is search/schedule based rather than true event delivery.

---

# 31. Jira RSS

Jira filter/search results can be consumed through RSS export.

Architecture:

```text
internal consumer
    |
    | outbound HTTPS polling
    v
Jira RSS
```

Advantages:

- simple
- no inbound endpoint
- usable by RSS readers or software

Limitations:

- feed semantics rather than full canonical API
- less structured than REST
- authentication/permissions still apply

For reliable sync, REST/JQL is usually preferable.

---

# 32. Confluence RSS

Confluence notification/RSS documentation:
https://support.atlassian.com/confluence-cloud/docs/get-notified-about-content-and-activity/

Confluence offers predefined and custom RSS feeds for content/activity.

Private consumers can poll the feed with outbound HTTPS.

Good for:

- lightweight content-change awareness

Not ideal for:

- authoritative content synchronization
- exact event processing guarantees

Use Forge events or REST/CQL for stronger machine workflows.

---

# 33. Slack notifications

## Jira

Official docs:
https://support.atlassian.com/jira-software-cloud/docs/get-jira-notifications-in-slack/

The Jira Slack app can provide:

- personal notifications
- channel notifications
- assignment/mention/comment/watch/status-change updates depending on configuration

## Confluence

Official docs:
https://support.atlassian.com/confluence-cloud/docs/use-slack-and-confluence-together/

Confluence supports:

- personal Slack notifications
- page/content subscriptions to Slack channels
- space subscriptions
- configurable event types

No custom server is required.

---

# 34. Microsoft Teams

Jira official documentation:
https://support.atlassian.com/jira-software-cloud/docs/get-jira-notifications-in-microsoft-teams/

Users can receive:

- personal Jira notifications
- team/channel notifications
- per-space/project filtering
- interactive work-item cards

This is human notification rather than a durable machine event log.

---

# 35. Direct Jira REST polling

If no app installation, public callback, or JSM connector is permitted, direct polling is often the safest architecture.

Use enhanced Jira JQL search:

```http
POST /rest/api/3/search/jql
```

Example:

```jql
project = MOBILE
AND updated >= "2026-10-01 16:00"
ORDER BY updated ASC
```

Persist a checkpoint and use an overlap window.

Concept:

```text
checkpoint = 16:00
query from 15:55
deduplicate by issue key + version/updated
advance checkpoint after successful page processing
```

Why overlap?

- indexing delay
- boundary timestamps
- request failure
- race conditions

No public endpoint is required.

---

# 36. Direct Confluence REST/CQL polling

Use CQL to discover changed content, then REST v2 to retrieve canonical entities.

Conceptual CQL:

```text
space = "ENG"
AND lastmodified >= ...
ORDER BY lastmodified ASC
```

Store:

- content ID
- version
- last-modified timestamp
- source URL

Then only retrieve full body/attachments/comments when needed.

Again, outbound HTTPS only.

---

# 37. Webhook + reconciliation is stronger than webhook alone

For important sync pipelines:

```text
fast path:
webhook or Forge trigger

correctness path:
periodic REST reconciliation
```

Why?

- duplicates
- out-of-order delivery
- retries
- temporary receiver failure
- cascade behavior
- evolving payloads

Never assume a webhook feed is a perfect append-only transaction log unless Atlassian explicitly documents that property.

---

# 38. Forge inbox architecture — recommended for Jira + Confluence

If both Jira and Confluence must feed a private agent/server:

```text
                  ATLASSIAN
+--------------------------------------------+
| Jira + Confluence                         |
|       |                                    |
|       v                                    |
| Forge product triggers                    |
|       |                                    |
|       v                                    |
| Forge Async Queue                         |
|       |                                    |
|       v                                    |
| Forge Storage event inbox                 |
|       |                                    |
| Forge App REST API (Atlassian hosted)     |
+-------^------------------------------------+
        |
        | outbound HTTPS
        |
+-------+------------------------------------+
| CORPORATE NETWORK                         |
| Event collector                           |
|   +--> local queue                        |
|   +--> REST enrichment                    |
|   +--> search/vector index                |
|   +--> custom AI agent                    |
+--------------------------------------------+
```

Properties:

- no inbound corporate URL
- both Jira and Confluence supported
- event-driven capture
- local processing still possible
- REST reconciliation available

Main caveat: Forge App REST API is currently Preview.

---

# 39. JEC architecture — recommended for JSM → LAN script

```text
JSM event
   |
   v
Automation
   |
   v
JEC queue
   ^
   |
   | outbound poll
   |
internal JEC
   |
   v
Python / PowerShell / Shell / executable
```

If you already have JSM Premium/Enterprise, this may be the lowest-complexity Atlassian-native answer to:

> “Run internal code whenever Jira event X happens, without exposing a server.”

---

# 40. AWS queue architecture — recommended for AWS shops

```text
Jira
  |
  v
Automation
  |
  v
SNS
  |
  v
SQS ------> DLQ
  ^
  |
private worker
```

Benefits:

- no public listener
- durable buffering
- IAM
- replay/retry patterns
- independent worker scaling

Use Jira REST after dequeuing to fetch canonical current state.

---

# 41. Organization-audit architecture

For security/admin monitoring:

```text
private collector
      |
      | outbound GET + cursor
      v
/api.atlassian.com/admin/v1/orgs/{orgId}/events-stream
```

Use this instead of a webhook when security prohibits inbound endpoints.

Do not substitute it for complete issue/page event capture.

---

# 42. Managed webhook relay

If your policy only prohibits exposing the **corporate network**, a managed Internet endpoint can terminate the webhook:

```text
Jira webhook
     |
     v
managed HTTPS ingress
     |
     v
managed queue
     ^
     |
internal consumer pulls
```

Generic building blocks:

- AWS API Gateway/Lambda → SQS
- Azure Function → Service Bus
- Google Cloud Run/Function → Pub/Sub
- Cloudflare Worker → managed queue
- dedicated webhook relay/broker services

This means:

```text
public URL on corporate server: NO
public URL somewhere: YES
```

Security controls:

- signature/auth verification
- source restrictions where appropriate
- TLS
- rate limiting
- dedup
- short sensitive-data retention
- queue DLQ
- secrets rotation

---

# 43. Reverse tunnels

Examples include ngrok/Cloudflare Tunnel-like products.

Flow:

```text
Atlassian
   |
   v
public tunnel URL
   |
   | outbound-established tunnel
   v
private server
```

Classification:

| Requirement | Result |
|---|---|
| Do I need an inbound firewall/NAT port on origin? | No |
| Is origin IP directly exposed? | Usually no |
| Does a public URL exist? | **Yes** |
| Does it satisfy strict “no public URL”? | **No** |

Use only if the real security requirement is “no inbound firewall exposure,” not “no Internet-reachable endpoint.”

---

# 44. Atlassian Application Tunnels

Atlassian provides Application Tunnels for specific Atlassian Cloud ↔ Atlassian Data Center connectivity scenarios.

They allow Data Center products behind a private network to participate in supported application-link scenarios without opening normal inbound connections.

They are **not documented as a generic tunnel for arbitrary custom webhook receivers**.

Do not assume an internal custom service can become a Jira webhook endpoint through Application Tunnels.

---

# 45. Confluence Data Center webhooks

Confluence Data Center differs from Cloud.

Official webhook guide:
https://developer.atlassian.com/server/confluence/webhooks/

Current Data Center REST family includes operations such as:

```http
GET    /rest/api/webhooks
POST   /rest/api/webhooks
GET    /rest/api/webhooks/{webhookId}
PUT    /rest/api/webhooks/{webhookId}
DELETE /rest/api/webhooks/{webhookId}

GET    /rest/api/webhooks/{webhookId}/latest
GET    /rest/api/webhooks/{webhookId}/statistics
GET    /rest/api/webhooks/{webhookId}/statistics/summary
POST   /rest/api/webhooks/test
```

If both Confluence Data Center and the receiver are inside the same private network, internal-only callback addresses can be routable within that environment.

Do not copy the Data Center API assumption to Confluence Cloud.

---

# 46. Confluence Cloud webhook registration

I did not find a current general Confluence Cloud REST v1/v2 webhook-management resource analogous to Jira Cloud's `/rest/api/3/webhook`.

For a **new Confluence Cloud event integration**, the current strategic Atlassian path is Forge triggers.

Legacy Connect webhook modules exist in historical/current migration documentation, but Connect is not the preferred architecture for new extensibility.

---

# 47. MCP v2 is not an event-subscription API

Current tool catalog:
https://support.atlassian.com/atlassian-ai-gateway/docs/supported-tools/

Atlassian MCP v2 exposes request/response tools for:

- Jira read/search/write/manage/etc.
- Confluence read/search/write
- Teamwork Graph context/search

The current catalog does **not** document generic product-event tools such as:

```text
subscribeToJiraEvents
subscribeToConfluenceEvents
```

Therefore:

- use MCP to let an agent inspect/action Atlassian
- use Forge/webhook/polling/JEC/Automation for event arrival

An MCP transport connection should not be interpreted as a Jira/Confluence change stream.

---

# 48. There is no documented general external Jira/Confluence SSE/Kafka feed

Current public Atlassian interfaces should be understood as:

| Interface | Actual behavior |
|---|---|
| Jira webhook | outbound HTTP callback |
| Forge trigger | Atlassian-hosted queued invocation |
| Organization `/events-stream` | paginated HTTP polling |
| Forge Async Events | app-internal queue |
| Forge Realtime | WebSocket channels for Forge app UI |
| RSS | pull feed |
| MCP | request/response agent tooling |

I found no documented general external Jira/Confluence product-change Kafka, AMQP, or SSE subscription endpoint for arbitrary clients.

---

# 49. Human-notification comparison

| Mechanism | Jira | Confluence | Your server required? |
|---|---:|---:|---:|
| Email | Yes | Yes | No |
| Watchers | Yes | Yes | No |
| Jira notification schemes | Yes | N/A | No |
| Custom Jira workflow email event | Yes | N/A | No |
| Confluence page/space watch | N/A | Yes | No |
| Saved-filter scheduled email | Yes | N/A | No |
| RSS | Yes | Yes | No |
| Slack | Yes | Yes | No |
| Microsoft Teams | Yes | Jira official support; Confluence can use Automation | No internal endpoint |
| Twilio via Automation | Yes | Yes | No internal endpoint |
| JSM incident/on-call notification | JSM | N/A | No |

---

# 50. Machine-notification comparison

| Mechanism | Transport | Public receiver required? | Durability model | Best for |
|---|---|---:|---|---|
| Jira webhook | HTTP push | Yes | retries | operational integration |
| Audit webhook | HTTP push | Yes | limited retries | SIEM |
| Forge trigger | Atlassian event invocation | No | platform queue/retries | app processing |
| Forge Async Events | internal queue | No | queue | deferred app processing |
| Org events-stream | HTTP pull | No | cursor | audit collector |
| REST JQL/CQL | HTTP pull | No | your checkpoint | canonical sync |
| JEC | outbound poll → local execution | No | connector queue | on-prem JSM action |
| Automation → SNS/SQS | managed queue | No | durable queue | enterprise Jira |
| RSS | HTTP pull | No | feed | lightweight monitoring |
| Email | mail | No | mailbox | human/low-criticality automation |

---

# 51. Event payload should not be your canonical database

Recommended pattern:

```text
event received
    |
    v
deduplicate
    |
    v
identify object
    |
    v
GET canonical current state through REST
    |
    v
update local index/database
```

Reasons:

- payloads vary
- delivery can be delayed
- ordering is not guaranteed in all systems
- duplicates occur
- multiple updates can happen before consumption
- cascade events can be incomplete
- permissions/schema evolve

---

# 52. Idempotency

Never assume exactly-once delivery.

For Jira webhook:

```text
dedup key = X-Atlassian-Webhook-Identifier
```

For audit events:

```text
dedup key = event.id
```

For your own Forge inbox:

```text
dedup key = source + stable event identity
```

Example:

```sql
CREATE TABLE processed_events (
    source TEXT NOT NULL,
    event_id TEXT NOT NULL,
    processed_at TIMESTAMP NOT NULL,
    PRIMARY KEY (source, event_id)
);
```

---

# 53. Ordering

Do not assume arrival order equals source-state order.

Prefer:

- entity version
- update timestamp
- fresh REST fetch
- processed cursor for audit stream

Example:

```text
update N+1 arrives before update N
        |
        v
REST fetch retrieves current state
        |
        v
local model remains correct
```

---

# 54. Public webhook security

If you use direct webhooks:

1. HTTPS.
2. Validate the documented authentication/signature mechanism.
3. Allowlist Atlassian egress ranges when appropriate.
4. Treat JSON as untrusted input.
5. Return quickly.
6. Queue work.
7. Deduplicate.
8. Rate-limit.
9. Protect against replay.
10. Do not log secrets.
11. Use least-privilege OAuth scopes.
12. Monitor failed webhook delivery.
13. Reconcile periodically with REST.

---

# 55. Private/pull architecture security

Protect:

- Atlassian OAuth refresh/access tokens
- API tokens
- organization API keys
- JEC API key
- AWS credentials
- Forge App REST 3LO credentials

For queues:

- encryption
- IAM least privilege
- DLQ
- minimal event payloads
- retention policies
- monitoring of queue age/lag

For Forge:

- minimum scopes
- trigger filters
- `ignoreSelf`
- scoped Realtime channels
- avoid unnecessary external egress

---

# 56. Recommended solution for a private custom agent

Assume:

- Jira + Confluence Cloud
- custom agent inside a corporate network
- inbound Internet is prohibited
- event awareness should be relatively fast

Preferred architecture:

```text
                 ATLASSIAN CLOUD
+---------------------------------------------+
| Jira / Confluence                          |
|       |                                     |
|       v                                     |
| Forge triggers                             |
|       |                                     |
|       v                                     |
| Forge Async Queue / Storage event inbox    |
|       |                                     |
| Atlassian-hosted App REST API (optional)   |
+-------^-------------------------------------+
        |
        | outbound HTTPS only
        |
+-------+-------------------------------------+
| PRIVATE NETWORK                            |
| Collector                                  |
|   +--> local durable queue                 |
|   +--> Jira/Confluence REST enrichment     |
|   +--> index/vector database               |
|   +--> custom AI agent                     |
+---------------------------------------------+
```

If Forge App REST Preview status is unacceptable, simplify to:

```text
private worker
   +--> Jira JQL polling
   +--> Confluence CQL polling
   +--> optional audit /events-stream
```

---

# 57. Recommended Jira-only private architecture with AWS

```text
Jira
 |
 v
Automation
 |
 v
SNS
 |
 v
SQS ----> DLQ
 ^
 |
private collector
 |
 +--> Jira REST canonical fetch
 +--> custom processing
```

This is excellent where AWS is already approved.

---

# 58. Recommended JSM private architecture

```text
JSM event
  |
  v
Automation
  |
  v
JEC
  ^
  |
  | outbound polling
  |
internal connector
  |
  v
local script
```

If your Jira environment includes JSM Premium/Enterprise, test this first for on-prem execution.

---

# 59. Recommended zero-app architecture

```text
internal worker
   |
   +--> POST /rest/api/3/search/jql
   |
   +--> Confluence CQL + REST v2
   |
   +--> GET /admin/v1/orgs/{orgId}/events-stream (if audit data matters)
```

Suggested conceptual frequencies:

```text
Jira changes:      every 1–5 min
Confluence:        every 2–10 min
Audit events:      every 1–5 min
Full reconciliation: periodically/nightly
```

Choose actual frequency from business latency and rate-limit requirements.

---

# 60. Decision guide

### Need very low latency and a reachable managed ingress is acceptable

Use a Jira webhook, possibly terminating at a cloud relay/queue.

### No corporate public ingress, Jira + Confluence both matter

Use Forge product triggers.

### No corporate ingress, processing must happen in the LAN

Consider in this order:

1. JEC if JSM Premium/Enterprise and the use case fits
2. Forge event inbox + private puller
3. Jira Automation → SNS/SQS
4. direct REST polling

### Security/audit events

Use organization `/events-stream`.

### Human notifications

Use native email/watchers, Slack, Teams, saved-filter subscriptions, or RSS.

### Live updates in an open Forge UI

Use Forge Realtime.

---

# 61. Suggested PoCs

## PoC A — JEC

If JSM Premium/Enterprise is available:

```text
issue transition
 -> Automation
 -> Run script using JEC
 -> local Python script
```

Measure:

- latency
- message loss/retry behavior
- worker downtime behavior
- payload size/richness
- throughput
- security approval

## PoC B — Forge Jira + Confluence

```text
Jira/Confluence event
 -> Forge trigger
 -> Async Queue
 -> Storage
```

Measure:

- trigger latency
- event coverage
- scopes
- payloads
- quota usage
- cascade behavior

## PoC C — private retrieval

Try Forge App REST API if Preview is acceptable.

Otherwise compare direct JQL/CQL polling.

## PoC D — AWS queue

```text
Jira Automation
 -> SNS
 -> SQS
 -> private consumer
```

Measure:

- IAM setup
- Automation execution quota
- queue latency
- DLQ behavior
- payload design

---

# 62. Official documentation registry

## Jira webhooks

Guide:
https://developer.atlassian.com/cloud/jira/platform/webhooks/

REST:
https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-webhooks/

Dynamic REST operations:

```text
GET    /rest/api/3/webhook
POST   /rest/api/3/webhook
DELETE /rest/api/3/webhook
PUT    /rest/api/3/webhook/refresh
GET    /rest/api/3/webhook/failed
```

Admin family:

```text
/rest/webhooks/1.0/webhook
```

---

## Forge events

Overview:
https://developer.atlassian.com/platform/forge/events-reference/

Trigger:
https://developer.atlassian.com/platform/forge/manifest-reference/modules/trigger/

Jira:
https://developer.atlassian.com/platform/forge/events-reference/jira/

Jira Software:
https://developer.atlassian.com/platform/forge/events-reference/jira-software/

JSM:
https://developer.atlassian.com/platform/forge/events-reference/jira-service-management/

Confluence:
https://developer.atlassian.com/platform/forge/events-reference/confluence/

---

## Forge Dynamic Modules

https://developer.atlassian.com/platform/forge/apis-reference/dynamic-modules/

https://developer.atlassian.com/platform/forge/apis-reference/dynamic-modules-api/

---

## Forge Async Events

https://developer.atlassian.com/platform/forge/runtime-reference/async-events-api/

---

## Forge Realtime

https://developer.atlassian.com/platform/forge/realtime/

https://developer.atlassian.com/platform/forge/runtime-reference/realtime-events-api/

https://developer.atlassian.com/platform/forge/apis-reference/ui-api-bridge/realtime/

---

## Forge App REST APIs

https://developer.atlassian.com/platform/forge/app-rest-apis/

https://developer.atlassian.com/platform/forge/expose-forge-app-rest-apis/

https://developer.atlassian.com/platform/forge/access-rest-apis-exposed-by-a-forge-app/

---

## Organization audit Events API

https://developer.atlassian.com/cloud/admin/organization/rest/api-group-events/

Polling migration:
https://developer.atlassian.com/cloud/admin/organization/poll-events-migration/

Endpoints discussed:

```text
GET /v1/orgs/{orgId}/events
GET /v1/orgs/{orgId}/events/{eventId}
GET /v1/orgs/{orgId}/event-actions
GET /v1/orgs/{orgId}/events-stream
```

Audit webhooks:
https://support.atlassian.com/security-and-access-policies/docs/learn-more-about-audit-log-webhooks/

Audit exclusions:
https://support.atlassian.com/security-and-access-policies/docs/manage-event-exclusions-from-your-audit-log/

---

## Automation

Jira actions:
https://support.atlassian.com/cloud-automation/docs/jira-automation-actions/

Third-party connections:
https://support.atlassian.com/cloud-automation/docs/what-third-party-applications-can-i-integrate-with/

Confluence actions:
https://support.atlassian.com/cloud-automation/docs/actions-in-confluence-automation/

---

## Jira Edge Connector

Overview:
https://support.atlassian.com/jira-service-management-cloud/docs/jira-edge-connector-jec-an-extensibility-platform/

Automation:
https://support.atlassian.com/cloud-automation/docs/use-jira-automation-with-jira-edge-connector-jec/

Install:
https://support.atlassian.com/jira-service-management-cloud/docs/install-jira-edge-connector/

Configure:
https://support.atlassian.com/jira-service-management-cloud/docs/configure-jira-edge-connector/

Repository:
https://github.com/atlassian/jec

---

## Human notifications

Jira notification schemes:
https://support.atlassian.com/jira-cloud-administration/docs/configure-notification-schemes/

Jira email:
https://support.atlassian.com/jira-cloud-administration/docs/configure-email-notifications/

Jira custom events:
https://support.atlassian.com/jira-cloud-administration/docs/configure-email-notifications-for-a-custom-event/

Jira saved filters:
https://support.atlassian.com/jira-software-cloud/docs/save-your-search-as-a-filter/

Jira Slack:
https://support.atlassian.com/jira-software-cloud/docs/get-jira-notifications-in-slack/

Jira Teams:
https://support.atlassian.com/jira-software-cloud/docs/get-jira-notifications-in-microsoft-teams/

Confluence watches:
https://support.atlassian.com/confluence-cloud/docs/watch-pages-spaces-and-blogs/

Confluence email:
https://support.atlassian.com/confluence-cloud/docs/subscribe-to-email-notifications/

Confluence RSS/notifications:
https://support.atlassian.com/confluence-cloud/docs/get-notified-about-content-and-activity/

Confluence Slack:
https://support.atlassian.com/confluence-cloud/docs/use-slack-and-confluence-together/

---

## Confluence Data Center webhook API

https://developer.atlassian.com/server/confluence/webhooks/

---

## MCP

https://support.atlassian.com/atlassian-ai-gateway/docs/supported-tools/

MCP is currently a tool invocation interface, not a general Jira/Confluence event subscription service.

---

# 63. Final recommendation

For a **custom Jira + Confluence agent inside a private corporate network**, my preferred architecture is:

```text
Forge product-event triggers
        +
Forge Async Queue / Storage
        +
private outbound pull when local processing is required
        +
periodic REST reconciliation
```

For **JSM → internal scripts**, test **Jira Edge Connector** first because Atlassian built it specifically for systems not accessible from the public Internet.

For **Jira in an AWS environment**, **Automation → SNS → SQS → private worker** is a robust and simple queue-based pattern.

For **organization/security/audit events**, use the cursor-based **`/events-stream` polling API**.

For **the least platform complexity**, use **JQL/CQL polling with checkpoints**.

The central rule is:

> Standard HTTP webhooks require a routable HTTPS receiver. If exposing one is forbidden, move handling into Atlassian, use a managed queue and pull it, or make the private side initiate the communication.

---

## Freshness notes

- Research reflects public Atlassian documentation available on **1 October 2026**.
- Forge Dynamic Modules and Forge App REST APIs are currently Preview capabilities.
- Exact quotas, plan entitlements, and event lists can change; use the linked live documentation before production rollout.
- Connect is included only where relevant to legacy/migration behavior.
