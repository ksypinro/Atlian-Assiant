
# Deep Research: Fetching Data from Atlassian Jira and Confluence

**Research date:** 1 October 2026  
**Primary scope:** Atlassian Cloud (Jira Cloud, Jira Software Cloud, Jira Service Management Cloud, and Confluence Cloud)  
**Secondary scope:** Jira/Confluence Data Center where the access method materially differs  
**Focus:** reading/fetching data, search, bulk extraction, agent access, automation, and corresponding write capabilities where they help evaluate an integration method.

> This report distinguishes **documented public interfaces** from UI automation and third-party tools. For production integrations, prefer documented REST/MCP/GraphQL/Data Lake/Forge interfaces rather than reverse-engineering private browser network calls.

---

## 1. Executive summary

There is no single best access method for every Jira/Confluence use case.

| Method | Jira | Confluence | Best for | Main limits |
|---|---|---|---|---|
| REST APIs | Excellent | Excellent | Deterministic integrations, ETL, services, scripts | Pagination, scopes, rate limits, product-specific APIs |
| Atlassian MCP v2 | Excellent | Excellent | AI agents, IDE agents, natural-language workflows | Tool catalog is narrower than full REST; OAuth/admin settings; some graph/search calls use Rovo credits |
| Integrated browser / computer-use | Good for UI-visible data | Good for UI-visible data | One-off tasks and UI-only workflows | Slow, brittle, poor for bulk sync; SSO/MFA/DOM changes |
| Atlassian CLI (ACLI) | Strong | No general Confluence command tree in current ACLI reference | Human/CI Jira work | Jira-focused |
| Teamwork Graph CLI (`twg`) | Strong | Strong | Agent-first shell access across Atlassian | Cloud/network dependent; agent-oriented rather than REST parity |
| Atlassian GraphQL Gateway | Useful | Useful | Linked/cross-product queries | Schema-dependent coverage |
| Forge app APIs | Excellent | Excellent | Apps running inside Atlassian | Requires Forge lifecycle/scopes |
| Webhooks + Automation | Excellent for changes | Good for changes | Push/event-driven sync | Event coverage differs; pair with REST for canonical reads |
| Atlassian Data Lake / data shares | Excellent for analytics | Excellent for analytics | Warehousing, BI, historical analysis | Entitlement/admin requirements; different permission semantics |
| Native exports | Useful | Useful | Manual snapshots/migrations | Not suitable for continuous sync |
| Data Center REST/PAT | Excellent if self-hosted | Excellent if self-hosted | On-prem/Data Center integrations | Version-specific; Cloud MCP/TWG assumptions do not carry over |

### Practical selection

- **Reliable service or sync engine:** REST + webhooks. Use JQL/CQL for discovery and product REST endpoints for canonical detail.
- **AI coding/operations agent:** Atlassian MCP v2. Add REST only for endpoints that MCP does not expose or for high-volume deterministic reads.
- **Shell automation for Jira:** ACLI.
- **Agent shell access across Jira + Confluence:** Teamwork Graph CLI (`twg`).
- **Organization-scale analytics/warehouse:** Atlassian Data Lake / data shares.
- **Ad-hoc UI access:** browser automation can work, but should not be the main production extractor.
- **Embedded Atlassian app:** Forge using `requestJira`, `requestConfluence`, and `requestGraph`.

---

# 2. REST API access

REST is the broadest, most explicit and easiest-to-test way to integrate with Jira and Confluence Cloud.

## 2.1 Authentication

### 2.1.1 Basic authentication: email + API token

Suitable for an internal script, one-off integration, or manual testing.

```bash
export ATLASSIAN_EMAIL="you@example.com"
export ATLASSIAN_API_TOKEN="..."
export ATLASSIAN_SITE="example.atlassian.net"

curl --user "$ATLASSIAN_EMAIL:$ATLASSIAN_API_TOKEN" \
  --header "Accept: application/json" \
  "https://$ATLASSIAN_SITE/rest/api/3/myself"
```

Confluence example:

```bash
curl --user "$ATLASSIAN_EMAIL:$ATLASSIAN_API_TOKEN" \
  --header "Accept: application/json" \
  "https://$ATLASSIAN_SITE/wiki/api/v2/pages?limit=25"
```

Password-based Basic authentication is deprecated in Cloud; the credential pair is email + API token. Atlassian recommends OAuth/app auth for distributable integrations.

Official docs:

- Jira: https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/
- Confluence: https://developer.atlassian.com/cloud/confluence/basic-auth-for-rest-apis/
- JSM: https://developer.atlassian.com/cloud/jira/service-desk/basic-auth-for-rest-apis/

### 2.1.2 OAuth 2.0 authorization code grant (3LO)

Use for an application acting on behalf of users. The user consents to scopes and the app receives an access token.

Jira 3LO API gateway form:

```text
https://api.atlassian.com/ex/jira/{cloudId}/rest/api/3/...
```

Example:

```bash
curl \
  "https://api.atlassian.com/ex/jira/$CLOUD_ID/rest/api/3/myself" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Accept: application/json"
```

Docs:

- https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/
- https://developer.atlassian.com/cloud/confluence/scopes-for-oauth-2-3LO-and-forge-apps/

### 2.1.3 Forge-managed authentication

Inside Forge, use the product fetch functions instead of manually constructing product auth headers:

```javascript
import api, { route } from "@forge/api";

const jira = await api.asUser().requestJira(
  route`/rest/api/3/issue/PROJ-123`
);

const confluence = await api.asUser().requestConfluence(
  route`/wiki/api/v2/pages/123456`
);
```

`asUser()` acts with user context; `asApp()` acts with app context within granted permissions/scopes.

Docs: https://developer.atlassian.com/platform/forge/runtime-reference/fetch-api/

---

# 3. Jira Cloud REST

Jira data is split across three public API families:

1. **Jira Cloud Platform REST API v3**
2. **Jira Software Cloud REST API**
3. **Jira Service Management Cloud REST API**

## 3.1 Jira Cloud Platform REST API v3

Direct base:

```text
https://{site}.atlassian.net/rest/api/3
```

Official docs:

- REST reference: https://developer.atlassian.com/cloud/jira/platform/rest/v3/
- OpenAPI: https://dac-static.atlassian.com/cloud/jira/platform/swagger-v3.v3.json

The OpenAPI file is the machine-readable operation inventory and is the best source for generating a complete client/API registry that stays aligned with Atlassian.

### Current Jira Platform REST v3 resource groups

- App data policies
- App migration
- App properties
- Application roles
- Audit records
- Avatars
- Classification levels
- Dashboards
- Dynamic modules
- Field schemes
- Filter sharing
- Filters
- Group and user picker
- Groups
- Issue attachments
- Issue bulk operations
- Issue comment properties
- Issue comments
- Issue custom field associations
- Issue custom field configuration (apps)
- Issue custom field contexts
- Issue custom field options
- Issue custom field options (apps)
- Issue custom field values (apps)
- Issue field configurations
- Issue fields
- Issue link types
- Issue links
- Issue navigator settings
- Issue notification schemes
- Issue panels
- Issue priorities
- Issue properties
- Issue redaction
- Issue remote links
- Issue resolutions
- Issue search
- Issue security level
- Issue security schemes
- Issue type properties
- Issue type schemes
- Issue type screen schemes
- Issue types
- Issue votes
- Issue watchers
- Issue worklog properties
- Issue worklogs
- Issues
- JQL
- JQL functions (apps)
- Jira expressions
- Jira settings
- Labels
- License metrics
- Migration of Connect modules to Forge
- Myself
- Permission schemes
- Permissions
- Plans
- Priority schemes
- Project avatars
- Project categories
- Project classification levels
- Project components
- Project email
- Project features
- Project key and name validation
- Project permission schemes
- Project properties
- Project role actors
- Project roles
- Project templates
- Project types
- Project versions
- Projects
- Screen schemes
- Screen tab fields
- Screen tabs
- Screens
- Server info
- Service Registry
- Status
- Tasks
- Teams in plan
- Time tracking
- UI modifications (apps)
- User properties
- User search
- Users
- Webhooks
- Workflow scheme drafts
- Workflow scheme project associations
- Workflow schemes
- Workflow status categories
- Workflow statuses
- Workflow transition rules
- Workflows
- Other operations

### High-value read patterns

#### Read one issue/work item

```http
GET /rest/api/3/issue/{issueIdOrKey}
```

```bash
curl --user "$ATLASSIAN_EMAIL:$ATLASSIAN_API_TOKEN" \
  -H "Accept: application/json" \
  "https://$ATLASSIAN_SITE/rest/api/3/issue/PROJ-123?fields=summary,status,assignee,description"
```

Project only the fields you need in bulk jobs.

#### Search with JQL — use enhanced search for new code

Preferred:

```http
GET  /rest/api/3/search/jql
POST /rest/api/3/search/jql
```

Example:

```bash
curl --user "$ATLASSIAN_EMAIL:$ATLASSIAN_API_TOKEN" \
  --request POST \
  --header "Accept: application/json" \
  --header "Content-Type: application/json" \
  --data '{
    "jql": "project = PROJ AND updated >= -7d ORDER BY updated DESC",
    "fields": ["summary","status","assignee","updated"],
    "maxResults": 100
  }' \
  "https://$ATLASSIAN_SITE/rest/api/3/search/jql"
```

The enhanced endpoint uses `nextPageToken`; pass the returned token to retrieve the next page. It also supports `reconcileIssues` when stronger read-after-write consistency is needed.

The legacy `GET/POST /rest/api/3/search` operations are currently being removed. Do not build new integrations around them.

Reference:  
https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/

#### JQL helper APIs

```text
GET/POST /rest/api/3/jql/autocompletedata
GET      /rest/api/3/jql/autocompletedata/suggestions
POST     /rest/api/3/jql/parse
POST     /rest/api/3/jql/pdcleaner
POST     /rest/api/3/jql/sanitize
POST     /rest/api/3/jql/match
```

Reference:  
https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-jql/

These are valuable for query builders and agents that should validate or sanitize JQL before executing it.

#### Comments

Typical paths:

```text
GET  /rest/api/3/issue/{issueIdOrKey}/comment
POST /rest/api/3/issue/{issueIdOrKey}/comment
GET  /rest/api/3/issue/{issueIdOrKey}/comment/{id}
PUT  /rest/api/3/issue/{issueIdOrKey}/comment/{id}
DEL  /rest/api/3/issue/{issueIdOrKey}/comment/{id}
```

Docs: https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-comments/

#### Attachments

Fetch issue metadata first, then download only the attachment content you need.

Docs: https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-attachments/

#### Metadata and administration

Important references:

- Projects: https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-projects/
- Fields: https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-fields/
- Users: https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-users/
- User search: https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-user-search/
- Workflows: https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-workflows/
- Status: https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-status/
- Permissions: https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-permissions/
- Webhooks: https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-webhooks/

### Atlassian Document Format (ADF)

Jira REST v3 rich-text fields such as descriptions/comments can be ADF documents rather than plain strings.

Docs: https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/

---

## 3.2 Jira Software Cloud REST API

Base family:

```text
/rest/agile/1.0/...
```

Reference: https://developer.atlassian.com/cloud/jira/software/rest/

### Resource groups

- Backlog
- Board
- Epic
- Issue
- Sprint
- Development Information
- Feature Flags
- Deployments
- Builds
- Remote Links
- Security Information
- Operations
- DevOps Components

### Common reads

```http
GET /rest/agile/1.0/board
GET /rest/agile/1.0/board/{boardId}
GET /rest/agile/1.0/board/{boardId}/configuration
GET /rest/agile/1.0/board/{boardId}/sprint
GET /rest/agile/1.0/board/{boardId}/issue
GET /rest/agile/1.0/sprint/{sprintId}/issue
```

Example:

```bash
curl --user "$ATLASSIAN_EMAIL:$ATLASSIAN_API_TOKEN" \
  -H "Accept: application/json" \
  "https://$ATLASSIAN_SITE/rest/agile/1.0/board/42/sprint?state=active,future"
```

Docs:

- Board: https://developer.atlassian.com/cloud/jira/software/rest/api-group-board/
- Sprint: https://developer.atlassian.com/cloud/jira/software/rest/api-group-sprint/

The Jira Software reference also includes DevOps integration APIs for development information, feature flags, deployments, builds, remote links, security information and DevOps components. Some operations are app/module-oriented; verify each operation's scopes and app requirements before assuming an API token can call it.

---

## 3.3 Jira Service Management Cloud REST API

Base:

```text
/rest/servicedeskapi/...
```

Reference: https://developer.atlassian.com/cloud/jira/service-desk/rest/

### Resource groups

- Assets
- Customer
- Info
- Knowledgebase
- Organization
- Request
- Requesttype
- Servicedesk
- Other operations

### Common reads

```http
GET /rest/servicedeskapi/servicedesk
GET /rest/servicedeskapi/request
GET /rest/servicedeskapi/request/{issueIdOrKey}
GET /rest/servicedeskapi/request/{issueIdOrKey}/comment
GET /rest/servicedeskapi/request/{issueIdOrKey}/sla
GET /rest/servicedeskapi/knowledgebase/article
GET /rest/servicedeskapi/organization
GET /rest/servicedeskapi/info
```

Example:

```bash
curl --user "$ATLASSIAN_EMAIL:$ATLASSIAN_API_TOKEN" \
  -H "Accept: application/json" \
  "https://$ATLASSIAN_SITE/rest/servicedeskapi/knowledgebase/article?query=vpn&highlight=true&limit=50"
```

Docs:

- Request: https://developer.atlassian.com/cloud/jira/service-desk/rest/api-group-request/
- Service desk: https://developer.atlassian.com/cloud/jira/service-desk/rest/api-group-servicedesk/
- Knowledge base: https://developer.atlassian.com/cloud/jira/service-desk/rest/api-group-knowledgebase/
- Info: https://developer.atlassian.com/cloud/jira/service-desk/rest/api-group-info/

JSM sits on Jira Platform, so real integrations commonly use both `/rest/servicedeskapi/...` and `/rest/api/3/...`.

---

# 4. Confluence Cloud REST

For new content work, prefer v2 where it covers the required resource. Keep v1 available for important capabilities that remain there, especially classic CQL search routes.

## 4.1 Confluence REST API v2

Direct base:

```text
https://{site}.atlassian.net/wiki/api/v2
```

Docs:

- v2 reference: https://developer.atlassian.com/cloud/confluence/rest/v2/
- v2 OpenAPI: https://dac-static.atlassian.com/cloud/confluence/openapi-v2.v3.json

### Current v2 resource groups

- Admin Key
- Attachment
- App Properties
- Ancestors
- Blog Post
- Children
- Classification Level
- Comment
- Content
- Content Properties
- Custom Content
- Database
- Data Policies
- Descendants
- Folder
- Label
- Like
- Operation
- Page
- Redactions
- Smart Link
- Space
- Space Permissions
- Space Permission Transition
- Space Properties
- Space Roles
- Task
- User
- Version
- Whiteboard

### Page operations

```http
GET  /wiki/api/v2/pages
POST /wiki/api/v2/pages
GET  /wiki/api/v2/pages/{id}
PUT  /wiki/api/v2/pages/{id}
DEL  /wiki/api/v2/pages/{id}
GET  /wiki/api/v2/spaces/{id}/pages
GET  /wiki/api/v2/labels/{id}/pages
```

Example:

```bash
curl --user "$ATLASSIAN_EMAIL:$ATLASSIAN_API_TOKEN" \
  -H "Accept: application/json" \
  "https://$ATLASSIAN_SITE/wiki/api/v2/pages?space-id=12345&body-format=storage&limit=50"
```

Page docs:  
https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-page/

### Cursor pagination

Confluence v2 uses opaque cursor pagination. Collection results can expose the next URL in the `Link` response header and `_links.next`.

Pattern:

```text
GET /wiki/api/v2/pages?limit=50
GET <relative next URL returned by Atlassian>
```

Do not modify the opaque cursor.

### What v2 can fetch

Depending on permissions/resource:

- pages and blog posts
- versions
- spaces
- comments
- attachments
- ancestors, children and descendants
- labels and likes
- content properties
- custom content
- databases
- folders
- smart links
- whiteboards
- tasks
- users
- space permissions and roles
- classification/redaction/data-policy resources where applicable

The operation reference is authoritative for exact scopes and permissions.

---

## 4.2 Confluence v1 + CQL

CQL is a core discovery mechanism.

```http
GET /wiki/rest/api/search?cql={CQL}
```

The content-search form is also documented at:

```http
GET /wiki/rest/api/content/search?cql={CQL}
```

Example:

```bash
curl --user "$ATLASSIAN_EMAIL:$ATLASSIAN_API_TOKEN" \
  -G \
  -H "Accept: application/json" \
  --data-urlencode 'cql=space = "ENG" AND type = page ORDER BY lastmodified DESC' \
  --data-urlencode 'limit=50' \
  "https://$ATLASSIAN_SITE/wiki/rest/api/search"
```

CQL supports fields, operators, functions and SQL-like `ORDER BY`, but it is not SQL and has no `SELECT`.

Docs:

- Advanced CQL: https://developer.atlassian.com/cloud/confluence/advanced-searching-using-cql/
- REST v1: https://developer.atlassian.com/cloud/confluence/rest/v1/

Atlassian documents that some expansions can reduce effective search page size (for example, body-storage expansion in certain CQL queries). For large extraction: search IDs first, then fetch bodies in controlled batches.

---

# 5. Pagination, rate limits and efficient extraction

## 5.1 Pagination is API-specific

Do not write one universal paginator.

- Jira traditional endpoints often use `startAt`, `maxResults`, `total` and/or `isLast`.
- Jira enhanced JQL uses `nextPageToken`.
- Confluence v2 uses `cursor`, `Link` and `_links.next`.
- JSM pagination varies by resource.

Follow each endpoint's contract.

## 5.2 Rate limits

As of this research date, Jira and Confluence Cloud document points-based quotas plus burst limits for application traffic. Jira also documents per-issue write limits. Exceeded limits return HTTP `429`; obey `Retry-After`.

Jira's current docs describe:
- hourly points/quota limits
- burst limits
- per-issue write limits
- `RateLimit-Reason`, `X-RateLimit-*`, and `Retry-After` headers

Atlassian states the newer quota model applies to Forge, Connect and OAuth 2.0 integrations; API-token traffic is excluded from that points quota but still has applicable burst controls.

Docs:

- Jira: https://developer.atlassian.com/cloud/jira/platform/rate-limiting/
- Confluence: https://developer.atlassian.com/cloud/confluence/rate-limiting/

Recommended behavior:

```python
for attempt in range(MAX_RETRIES):
    response = call_atlassian()
    if response.status_code != 429:
        break
    wait = int(response.headers.get("Retry-After", 2 ** attempt))
    sleep(wait + jitter())
```

Also use bounded concurrency, field projection, incremental JQL/CQL and persistent checkpoints.

---

# 6. REST sync architectures

## 6.1 Jira incremental mirror

1. Persist `last_successful_sync`.
2. Search `updated >= "<checkpoint>" ORDER BY updated ASC`.
3. Use `POST /rest/api/3/search/jql`.
4. Select only required fields.
5. Follow `nextPageToken`.
6. Fetch comments/worklogs/changelogs/attachments only where needed.
7. Use a small overlap window to tolerate indexing/time boundaries.
8. Subscribe to webhooks for low latency.
9. Periodically reconcile with REST.

## 6.2 Confluence incremental mirror

1. Use CQL to identify changed content.
2. Store content ID, version and update timestamp.
3. Fetch current body only for changed objects.
4. Retrieve comments/attachments separately.
5. Preserve source permissions/restrictions in downstream authorization.
6. Use events/webhooks for low-latency updates where applicable.
7. Run periodic reconciliation.

---

# 7. Atlassian MCP v2

Atlassian operates an official remote MCP server for AI clients.

Server:

```text
https://mcp.atlassian.com/v2/mcp
```

For gateways that need every tool exposed as a flat paginated list:

```text
https://mcp.atlassian.com/v2/mcp?tools=all
```

Docs:

- Setup: https://support.atlassian.com/atlassian-ai-gateway/docs/get-started-with-the-atlassian-remote-mcp-server/
- Tool catalog: https://support.atlassian.com/atlassian-ai-gateway/docs/supported-tools/

## 7.1 MCP vs REST

REST exposes HTTP endpoint contracts. MCP exposes agent tools with schemas/descriptions.

MCP advantages:
- intent-driven tool selection
- dynamic discovery
- permission-group separation for read/write/search/delete/manage
- direct use by coding agents/LLMs
- cross-product Teamwork Graph context

MCP limitations:
- it does not equal every REST operation
- availability depends on authentication, scopes and admin settings
- enriched Rovo/Teamwork Graph calls can consume credits
- deterministic high-volume ETL is usually easier with REST/Data Lake

## 7.2 Primary/discovery tools

```text
atlassianUserInfo
getAccessibleAtlassianResources
discover
executeRead
executeWrite
executeDestructive
getContentFormatGuide
```

`getAccessibleAtlassianResources` returns accessible sites/apps and their `cloudId`s; Atlassian documents this as the required starting context because tools need a target cloud ID.

Typical agent flow:

```text
1. getAccessibleAtlassianResources()
2. select the intended cloudId
3. call a Primary tool OR discover("intent")
4. executeRead / executeWrite / executeDestructive for deferred tools
```

## 7.3 MCP authentication

### OAuth 2.1

Recommended for interactive clients.

```http
Authorization: Bearer <access_token>
```

Docs:  
https://support.atlassian.com/atlassian-ai-gateway/docs/configure-oauth-2-1/

### API-token authentication

Available when enabled by the organization admin.

Personal token:

```http
Authorization: Basic <base64(email:api_token)>
```

Service account API key:

```http
Authorization: Bearer <api_key>
```

Docs:  
https://support.atlassian.com/atlassian-ai-gateway/docs/configure-authentication-via-api-token/

Important differences from OAuth:
- some tools may be unavailable because their product scopes cannot be granted to token/API-key auth
- API tokens are not bounded to one `cloudId`; pass the target explicitly and carefully
- OAuth redirect/domain checks do not apply to token auth; IP allowlisting still matters

---

# 8. Connect MCP clients

## Codex

```bash
codex mcp add atlassian --url https://mcp.atlassian.com/v2/mcp
```

## Claude Code

```bash
claude mcp add --transport http atlassian https://mcp.atlassian.com/v2/mcp
```

Then invoke `/mcp` to authenticate.

## Generic / Claude Desktop-style config

```json
{
  "mcpServers": {
    "atlassian": {
      "url": "https://mcp.atlassian.com/v2/mcp"
    }
  }
}
```

## Windsurf-style config

```json
{
  "mcpServers": {
    "atlassian": {
      "serverUrl": "https://mcp.atlassian.com/v2/mcp/"
    }
  }
}
```

---

# 9. Jira MCP v2 tool catalog

## 9.1 `read_jira`

Scope:

```text
read:jira:agent-interface
```

- `getJiraIssue`
- `listJiraProjects`
- `listJiraProjectIssueTypesMetadata`
- `getJiraIssueTypeMetaWithFields`
- `listJiraIssueTransitions`
- `listJiraIssueLinkTypes`
- `listJiraIssueRemoteIssueLinks`
- `listJiraIssueWorklogs`
- `lookupJiraAccountId`
- `listJiraIssueAssignableUsers`
- `listJiraIssueComments`
- `listJiraIssueChangelogs`
- `getJiraCurrentUser`
- `getJiraUser`
- `listJiraStatuses`
- `listJiraProjectComponents`
- `getJiraProjectVersions`
- `getJiraProjectVersionRelatedWork`
- `listJiraBoards`
- `getJiraBoardConfig`
- `getJiraBoardIssueData`
- `getJiraBoardSprintData`
- `listJiraBoardSprints`
- `listJiraFilters`
- `listJiraDashboards`
- `getJiraEntityProperty`
- `downloadJiraIssueAttachment`

## 9.2 `search_jira`

Scope:

```text
search:jira:agent-interface
```

- `searchJiraIssuesUsingJql`

## 9.3 `write_jira`

Scope:

```text
write:jira:agent-interface
```

- `createJiraIssue`
- `editJiraIssue`
- `transitionJiraIssue`
- `addOrEditJiraIssueComment`
- `addOrEditJiraIssueWorklog`
- `createJiraIssueLink`
- `manageJiraProjectVersion`
- `manageJiraProjectVersionRelatedWork`
- `manageJiraSprint`
- `createJiraBoard`
- `watchJiraIssue`
- `uploadAttachmentToJiraIssue`
- `editJiraEntityProperty`
- `createJiraIssueRemoteIssueLink`
- `convertJiraIssueHierarchy`

## 9.4 `delete_jira`

Disabled by default; an admin must enable the permission group.

Scope:

```text
delete:jira:agent-interface
```

- `deleteJiraIssue`
- `deleteJiraComment`
- `deleteJiraIssueAttachment`

## 9.5 `manage_jira`

Disabled by default; an admin must enable it.

Scope:

```text
manage:jira:agent-interface
```

- `createJiraProject`
- `updateJiraProject`
- `getJiraWorkflows`
- `getJiraWorkflowEditorLink`
- `getJiraScreen`
- `updateJiraScreen`

### Jira MCP usage examples

```text
Find Jira issues in project MOBILE updated in the last 3 days.
Return key, summary, assignee, status, and latest comments.
```

An agent can use `searchJiraIssuesUsingJql`, then fetch detail/comments only when needed.

```text
Show the active sprint for the iOS board and summarize blocked work.
```

An agent can list/resolve boards, fetch sprint data and inspect board issues.

---

# 10. Confluence MCP v2 tool catalog

## 10.1 `read_confluence`

Scope:

```text
read:confluence:agent-interface
```

- `getConfluenceContent`
- `listConfluenceContent`
- `listConfluenceSpaces`
- `getConfluenceSpace`
- `getConfluenceSpaceInstructions`
- `getConfluencePersonalSpace`
- `listConfluenceComments`
- `getConfluenceComment`
- `listConfluenceContentVersions`
- `getConfluenceContentVersion`
- `diffConfluenceContentVersions`
- `listConfluenceAttachments`
- `getConfluenceAttachment`
- `downloadConfluenceAttachment`
- `exportConfluenceContent`
- `getConfluenceContentPermissions`
- `getConfluenceContentRestrictionState`
- `getConfluencePublicLinkStatus`
- `getConfluenceReactions`
- `getConfluenceTask`
- `listConfluenceTasks`
- `listConfluenceTemplates`
- `getConfluenceTemplate`
- `resolveConfluenceContentMacros`
- `getConfluenceMauiApp`

## 10.2 `search_confluence`

Scope:

```text
search:confluence:agent-interface
```

- `searchConfluence` — search with CQL.

## 10.3 `write_confluence`

Scope:

```text
write:confluence:agent-interface
```

- `createConfluenceContent`
- `updateConfluenceContent`
- `createConfluenceSpace`
- `setConfluenceSpaceInstructions`
- `copyConfluenceContent`
- `moveConfluenceContent`
- `archiveConfluenceContent`
- `unarchiveConfluenceContent`
- `restoreConfluenceContentVersion`
- `convertConfluenceContentMode`
- `setConfluenceContentStatus`
- `createConfluenceComment`
- `updateConfluenceComment`
- `updateConfluenceCommentResolution`
- `createConfluenceAttachment`
- `addLabelsToConfluenceContent`
- `addConfluenceReaction`
- `addConfluenceContentPermissions`
- `removeConfluenceContentPermissions`
- `replaceConfluenceContentPermissions`
- `setConfluenceContentRestrictionState`
- `enableConfluencePublicLink`
- `disableConfluencePublicLink`
- `completeConfluenceTask`
- `reopenConfluenceTask`
- `starConfluenceContent`
- `unstarConfluenceContent`
- `starConfluenceSpace`
- `unstarConfluenceSpace`
- `watchConfluenceContent`
- `unwatchConfluenceContent`
- `watchConfluenceSpace`
- `unwatchConfluenceSpace`
- `watchConfluenceLabel`
- `unwatchConfluenceLabel`
- `createConfluenceInfographicForPage`
- `editConfluenceInfographicForPage`
- `createConfluenceMauiApp`
- `editConfluenceMauiApp`

### Usage examples

```text
Find pages in the SECURITY space mentioning "threat model".
Return title, URL, latest version date and summary.
```

The agent can use `searchConfluence`, then `getConfluenceContent`.

```text
Fetch this Confluence page URL, show its restrictions and list attachments.
```

The agent can combine content, permission/restriction and attachment tools.

---

# 11. Teamwork Graph + Rovo tools via MCP

## Read graph

Scope:

```text
read:all:twg
```

- `getTeamworkGraphContext` — connected context/relationships for an Atlassian entity.
- `getTeamworkGraphObject` — fetch one or more objects by ARI or URL.

## Write graph

Scope:

```text
write:all:twg
```

- `addTeamworkGraphContext`

## Semantic Atlassian search

Scope:

```text
search:rovo:agent-interface
```

- `search` (beta) — semantic search across Jira, Confluence and connected apps.

Atlassian's current MCP docs state that enriched Teamwork Graph/Rovo context/search calls can consume Rovo credits and some calls can consume up to 10 credits.

Use graph tools when the user asks for **relationships/context**, not merely a known object's exact fields.

---

# 12. MCP usage guidance

## Use MCP when

- caller is an LLM/agent
- user expresses intent in natural language
- work crosses Jira and Confluence
- dynamic tool discovery is useful
- you want agent operations governed by Atlassian permission groups
- connected/graph context matters

## Prefer REST when

- deterministic schemas are required
- high-volume sync/ETL is required
- an endpoint is not exposed in MCP
- you need precise pagination, field projection, retries or caching
- you are building a normal non-agent microservice

A strong pattern is **MCP for orchestration + REST for specialized deterministic operations**.

---

# 13. Integrated browser / computer-use

An integrated browser is not an Atlassian data API. It is UI automation over Jira/Confluence.

## What it can read

A signed-in browser can generally access what the user can see in the UI, for example:

- Jira issue/work-item pages
- list/filter/JQL results
- boards, backlog and sprints
- dashboards
- Confluence pages, search and space navigation
- visible comments and attachments
- native CSV/PDF/Word exports
- admin screens when the user is an admin

## Typical flow

```text
1. Open the Atlassian site.
2. Complete SSO/MFA when required.
3. Navigate to Jira/Confluence.
4. Use product search, JQL/CQL or saved filters.
5. Read rendered data or trigger a native export.
6. Use this only where a documented API is not the better interface.
```

## Advantages

- little/no integration code
- uses the user's SSO session
- reaches UI-only workflows
- useful for one-off exploratory tasks

## Disadvantages

- DOM/components change
- virtualized lists/infinite scroll can omit data
- slower than APIs
- poor completeness guarantees for bulk extraction
- SSO/MFA/session expiration/CAPTCHA can interrupt execution
- high-impact UI actions can be accidental
- difficult to audit as a high-throughput data plane

## Do not build on private browser endpoints

Avoid reverse-engineering undocumented XHR/private GraphQL calls observed in DevTools. They may change without public compatibility guarantees. Prefer public REST, GraphQL, MCP, Forge or Data Lake.

---

# 14. Official CLI: Atlassian CLI (ACLI)

Reference:  
https://developer.atlassian.com/cloud/acli/reference/commands/

The current product command tree is Jira-focused; the reference does not expose a general `acli confluence ...` tree.

Current Jira areas include:

```text
acli jira auth
acli jira board
acli jira dashboard
acli jira field
acli jira filter
acli jira project
acli jira sprint
acli jira workitem
```

Work-item commands include archive/unarchive, assignment, attachments, cloning, comments, create/create-bulk, delete, edit, linking, search, transition, view and watcher operations.

Jira reference:  
https://developer.atlassian.com/cloud/acli/reference/commands/jira/

## Authentication

OAuth/browser:

```bash
acli jira auth login --web
```

API token via stdin:

```bash
echo "$ATLASSIAN_API_TOKEN" | \
  acli jira auth login \
  --site "example.atlassian.net" \
  --email "you@example.com" \
  --token
```

Auth reference:  
https://developer.atlassian.com/cloud/acli/reference/commands/jira-auth-login/

## Search/fetch

```bash
acli jira workitem search \
  --jql 'project = MOBILE ORDER BY updated DESC' \
  --paginate
```

```bash
acli jira workitem search \
  --jql 'assignee = currentUser() AND resolution = EMPTY' \
  --fields "key,summary,status,assignee" \
  --json
```

```bash
acli jira workitem search \
  --jql 'project = MOBILE' \
  --fields "key,summary,status" \
  --csv
```

ACLI is well suited to Jira shell scripts and CI when its command model covers the task.

---

# 15. Official CLI: Teamwork Graph CLI (`twg`)

TWG CLI is agent-first and spans Jira, Confluence and other Atlassian products.

Docs:

- Overview: https://support.atlassian.com/organization-administration/docs/what-is-twg-cli/
- Setup: https://support.atlassian.com/organization-administration/docs/get-started-with-twg-cli/
- Authentication: https://support.atlassian.com/organization-administration/docs/how-does-teamwork-graph-cli-authentication-work/
- MCP vs TWG: https://support.atlassian.com/atlassian-ai-gateway/docs/teamwork-graph-cli-and-atlassian-mcp-decision-guide/

Atlassian documents product coverage including Jira, Confluence, JSM, Assets, Bitbucket, Loom, Teamwork Graph/Rovo and other Atlassian platform products.

## Install and inspect

```bash
bash <(curl -fsSL https://teamwork-graph.atlassian.com/cli/install)
```

```bash
twg doctor
twg login
twg --help
twg jira --help
twg confluence --help
```

For coding agents:

```bash
twg skills install
```

Interactive authentication uses OAuth 2.1 by default.

Its normal UX is agent-driven natural language, e.g.:

```text
What are my open Jira work items?
Find pages about onboarding in Confluence.
Tell me everything about PROJ-123.
Summarize everything I worked on this week.
```

## ACLI vs TWG CLI

| Characteristic | ACLI | TWG CLI |
|---|---|---|
| Style | Human/command-first | Agent-first |
| Jira | Yes | Yes |
| Confluence | No general current command tree | Yes |
| Cross-product context | Limited | Core capability |
| Natural-language agent use | Possible wrapper | Designed for it |
| Exact REST parity | No | No |
| Cloud | Yes | Yes |

---

# 16. Community CLI

## `ankitpokhrel/jira-cli`

Repository:  
https://github.com/ankitpokhrel/jira-cli

A feature-rich community Jira CLI for Cloud and on-premises installations. The project documents issue/epic/sprint browsing and operations such as creation, cloning, linking and transition.

Cloud:

```bash
export JIRA_API_TOKEN="..."
jira init
jira issue list
```

For on-premises deployments the tool documents Basic, bearer/PAT and mTLS authentication modes where applicable.

Use a community tool when its UX materially helps and you can pin/test versions. For enterprise-critical automation, REST or an official CLI gives a clearer support boundary.

## Generic API CLIs

`curl`, HTTPie, Postman/Newman and language HTTP clients are also fully valid CLI approaches because they directly invoke documented REST endpoints.

---

# 17. Atlassian GraphQL Gateway

Atlassian exposes a platform GraphQL API for connected/cross-product data.

Docs:  
https://developer.atlassian.com/cloud/confluence/graphql/

Documented endpoints include:

```text
OAuth clients:
https://api.atlassian.com/graphql

Browser-session / API-token context:
https://{site}.atlassian.net/gateway/api/graphql
```

Generic call:

```bash
curl "https://api.atlassian.com/graphql" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"query":"query { ... }"}'
```

Advantages:
- field-selective responses
- graph traversal
- one cross-product abstraction

Limits:
- GraphQL schema coverage is not full REST parity
- schema fields can be beta/deprecated
- experimental fields can require a special header
- query cost/rate limits apply

Use Atlassian's GraphQL Explorer to validate the current schema before implementation.

---

# 18. Forge app access

Forge can call Jira, Confluence and Atlassian GraphQL with managed host authentication.

```javascript
import api, { route } from "@forge/api";

const issueRes = await api.asUser().requestJira(
  route`/rest/api/3/issue/${issueKey}`
);

const pageRes = await api.asUser().requestConfluence(
  route`/wiki/api/v2/pages/${pageId}`
);

const graphRes = await api.asUser().requestGraph(query, variables);
```

Docs:  
https://developer.atlassian.com/platform/forge/runtime-reference/fetch-api/

Best fit:
- embedded Atlassian app
- app governed by Atlassian installation/scopes
- integration where Atlassian should manage product authentication context

---

# 19. Webhooks and event-driven access

If the goal is to keep another system synchronized, use **events + REST**, not only aggressive polling.

## Jira dynamic webhooks

Documented management paths include:

```http
GET    /rest/api/3/webhook
POST   /rest/api/3/webhook
DELETE /rest/api/3/webhook
GET    /rest/api/3/webhook/failed
PUT    /rest/api/3/webhook/refresh
```

Docs:  
https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-webhooks/

Dynamic webhook registration is app-oriented and has lifecycle/expiry behavior; follow current Atlassian refresh requirements.

Recommended pipeline:

```text
Initial REST sync
  -> checkpoint
  -> webhook event
  -> queue entity ID/key
  -> fetch canonical current object via REST
  -> periodic reconciliation
```

Treat the event as a change signal; REST is normally the source for canonical current state.

## Confluence events/webhooks

Confluence app frameworks provide event/webhook mechanisms. The exact implementation depends on Forge or the legacy app model. Pair event notification with REST retrieval of the current content.

---

# 20. Atlassian Automation as an integration bridge

Jira and Confluence Automation can send outbound web requests. Jira Automation also supports incoming webhook triggers.

The Jira **Send web request** action can send:
- Jira-format work-item data
- Automation-format work-item data
- custom payloads

and can use the HTTP response in later rule steps.

Docs:

- Jira actions: https://support.atlassian.com/cloud-automation/docs/jira-automation-actions/
- Incoming/third-party integrations: https://support.atlassian.com/cloud-automation/docs/what-third-party-applications-can-i-integrate-with/
- Confluence actions: https://support.atlassian.com/cloud-automation/docs/actions-in-confluence-automation/

Use this when Atlassian is the event source and your external system just needs a push. It is not a replacement for a general bulk read API.

Security: tightly control who can edit automation rules that send potentially sensitive fields to external domains.

---

# 21. Atlassian Data Lake and data shares

For warehouse/BI use, Data Lake is often better than traversing every Jira issue and Confluence page through REST.

Docs:

- Data Lake: https://support.atlassian.com/analytics/docs/what-is-the-atlassian-data-lake/
- Data model: https://support.atlassian.com/analytics/docs/understand-the-data-model-of-the-atlassian-data-lake/
- Data shares: https://support.atlassian.com/analytics/docs/what-is-a-data-share/
- Create data share: https://support.atlassian.com/analytics/docs/create-a-data-share/
- Share model: https://support.atlassian.com/analytics/docs/understand-the-data-model-of-data-shares/

The Data Lake exposes structured data for Jira, JSM, Confluence and several other Atlassian products.

A **data share** is a read-only collection of Data Lake tables, connectable to environments/tools that support Databricks Delta Sharing.

### Best fit

```text
"Give me three years of Jira cycle-time data joined with Confluence activity,
grouped by team/month."
```

This is a warehouse query, not an agent loop over thousands of REST calls.

### Critical permission difference

Atlassian explicitly states that Analytics/Data Lake/data-share permissions do **not** mirror ordinary Jira/Confluence row-level permissions. Users of a connection/share may see data they would not ordinarily see in the products. Data-share inclusion is coarse.

Treat share credentials as high-value and implement downstream authorization/governance.

---

# 22. Native exports

## Jira

Jira UI search/list views can export CSV. Useful for a human one-off snapshot, migration or analysis.

## Confluence

Confluence supports page/space exports such as PDF, Word in applicable page flows, and space export formats such as PDF/CSV/HTML/XML depending on export type/permissions.

Use exports for:
- archives
- migrations
- one-time datasets

Do not use UI exports as a continuous integration protocol.

---

# 23. Data Center / self-hosted

Cloud and Data Center are distinct integration targets.

## Jira Data Center

Reference area:  
https://developer.atlassian.com/server/jira/platform/rest/

Basic auth docs:  
https://developer.atlassian.com/server/jira/platform/basic-authentication/

Depending on version/policy, Data Center supports authentication approaches including OAuth, PAT and Basic. Prefer PAT/OAuth over Basic for durable automation where supported.

## Confluence Data Center

REST reference:  
https://developer.atlassian.com/server/confluence/rest/

Typical path:

```text
http://host[:port]/{context}/rest/api/{resource}
```

CQL search is available in Data Center REST, e.g.:

```http
GET /rest/api/search?cql=...
```

### Cloud-only assumptions

Atlassian MCP v2, Teamwork Graph and Cloud API gateways are Cloud services. Do not assume those endpoints work against a self-hosted Data Center instance.

---

# 24. Direct database access

Not a Cloud option.

For Data Center, administrators control the product database, but direct DB access is not the normal integration API:

- schema is implementation/version dependent
- application permissions and derived logic can be bypassed
- direct writes can corrupt invariants
- upgrades can invalidate assumptions

Prefer Data Center REST/Java/plugin APIs. If read-only DB reporting is unavoidable, bind it to the exact deployed version and treat it as controlled analytics.

---

# 25. Decision matrix

| Use case | Primary choice | Secondary |
|---|---|---|
| Fetch one Jira issue / Confluence page | REST | MCP |
| Precise Jira search | JQL + REST | MCP JQL tool |
| Precise Confluence search | CQL + REST | MCP CQL tool |
| AI agent across Jira + Confluence | MCP v2 | TWG CLI |
| Human/CI Jira terminal | ACLI | community JiraCLI |
| Agent terminal across products | TWG CLI | MCP |
| Large incremental production sync | REST + webhooks | Data Lake when analytics-oriented |
| Organization BI/history | Data Lake/data share | REST ETL |
| Embedded Atlassian app | Forge | OAuth integration |
| Cross-product graph context | Teamwork Graph / GraphQL | multiple REST calls |
| One-off UI workflow | Integrated browser | native export |
| Self-hosted | Data Center REST/PAT | plugin APIs |

---

# 26. Permission/security semantics

## REST

Authenticated requests generally see only what the underlying user/app is allowed to see, plus scope restrictions.

## MCP

Effective access combines:
1. auth mechanism
2. MCP permission group/scope
3. product access
4. Jira/Confluence permissions
5. admin enablement for destructive/manage groups

## Browser

The automation has the effective rights of the signed-in UI session.

## Data Lake

Permission semantics materially differ: Data Lake/data shares are not row-level mirrors of product permissions. Apply explicit downstream governance.

---

# 27. Reference architecture: Jira + Confluence service

```text
                    +------------------+
                    | OAuth/API token  |
                    +--------+---------+
                             |
               +-------------+-------------+
               |                           |
      +--------v--------+         +--------v--------+
      | Jira REST v3    |         | Confluence v2  |
      | + Agile / JSM   |         | + v1 CQL       |
      +--------+--------+         +--------+--------+
               |                           |
               +-------------+-------------+
                             |
                    +--------v--------+
                    | Normalize IDs,  |
                    | perms, rich text|
                    +--------+--------+
                             |
                    +--------v--------+
                    | DB/search index |
                    +-----------------+
```

Engineering rules:
1. Persist `cloudId`, site and source IDs explicitly.
2. Prefer account IDs over mutable display names.
3. Normalize Jira ADF separately from Confluence body representations.
4. Store source URL + version.
5. Fetch only needed fields.
6. Handle 429/retry.
7. Queue webhook-driven refreshes.
8. Reconcile periodically.
9. Treat attachments as separate binary objects.
10. Do not log credentials or full sensitive payloads by default.

---

# 28. Reference architecture: AI agent

```text
User prompt
   |
   v
Agent
   +-- Atlassian MCP v2
   |     +-- Jira tools
   |     +-- Confluence tools
   |     +-- Teamwork Graph / Rovo search
   |
   +-- Optional REST service
         +-- specialized/high-volume calls not exposed in MCP
```

Recommended guardrails:
- start read-only
- require confirmation for writes
- always confirm destructive actions
- bind requests to the intended `cloudId`
- prefer exact JQL/CQL when the user gives exact filters
- cap and intentionally paginate results
- log tool metadata, not secrets
- download attachments on demand only

---

# 29. Reference architecture: analytics

If Atlassian Data Lake is available:
1. create a governed Analytics/Data Lake connection or data share
2. ingest into your BI/warehouse tool
3. transform/model downstream
4. use REST only for operational gaps
5. apply explicit row-level governance in your data platform

If it is not available:
1. incremental JQL/CQL
2. REST detail retrieval
3. webhooks/events for latency
4. checkpointed pagination
5. bounded parallelism and 429 handling
6. nightly/periodic reconciliation

---

# 30. API documentation registry

This is the authoritative link map for every API family discussed above.

## Jira Cloud Platform

- REST v3: https://developer.atlassian.com/cloud/jira/platform/rest/v3/
- OpenAPI: https://dac-static.atlassian.com/cloud/jira/platform/swagger-v3.v3.json
- Issue search: https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/
- JQL: https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-jql/
- Webhooks: https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-webhooks/
- Basic auth: https://developer.atlassian.com/cloud/jira/platform/basic-auth-for-rest-apis/
- OAuth 2.0 (3LO): https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/
- Rate limiting: https://developer.atlassian.com/cloud/jira/platform/rate-limiting/
- Atlassian Document Format: https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/

## Jira Software Cloud

- REST root: https://developer.atlassian.com/cloud/jira/software/rest/
- Board: https://developer.atlassian.com/cloud/jira/software/rest/api-group-board/
- Sprint: https://developer.atlassian.com/cloud/jira/software/rest/api-group-sprint/

## Jira Service Management Cloud

- REST root: https://developer.atlassian.com/cloud/jira/service-desk/rest/
- Requests: https://developer.atlassian.com/cloud/jira/service-desk/rest/api-group-request/
- Service desks: https://developer.atlassian.com/cloud/jira/service-desk/rest/api-group-servicedesk/
- Knowledge base: https://developer.atlassian.com/cloud/jira/service-desk/rest/api-group-knowledgebase/
- Basic auth: https://developer.atlassian.com/cloud/jira/service-desk/basic-auth-for-rest-apis/

## Confluence Cloud

- REST v2: https://developer.atlassian.com/cloud/confluence/rest/v2/
- REST v2 OpenAPI: https://dac-static.atlassian.com/cloud/confluence/openapi-v2.v3.json
- REST v1: https://developer.atlassian.com/cloud/confluence/rest/v1/
- CQL: https://developer.atlassian.com/cloud/confluence/advanced-searching-using-cql/
- Basic auth: https://developer.atlassian.com/cloud/confluence/basic-auth-for-rest-apis/
- Rate limiting: https://developer.atlassian.com/cloud/confluence/rate-limiting/
- GraphQL: https://developer.atlassian.com/cloud/confluence/graphql/

## Atlassian MCP

- MCP setup: https://support.atlassian.com/atlassian-ai-gateway/docs/get-started-with-the-atlassian-remote-mcp-server/
- Supported tools: https://support.atlassian.com/atlassian-ai-gateway/docs/supported-tools/
- OAuth 2.1: https://support.atlassian.com/atlassian-ai-gateway/docs/configure-oauth-2-1/
- API-token auth: https://support.atlassian.com/atlassian-ai-gateway/docs/configure-authentication-via-api-token/
- MCP/TWG decision guide: https://support.atlassian.com/atlassian-ai-gateway/docs/teamwork-graph-cli-and-atlassian-mcp-decision-guide/

## CLI

- ACLI: https://developer.atlassian.com/cloud/acli/reference/commands/
- ACLI Jira: https://developer.atlassian.com/cloud/acli/reference/commands/jira/
- ACLI auth: https://developer.atlassian.com/cloud/acli/reference/commands/jira-auth-login/
- TWG overview: https://support.atlassian.com/organization-administration/docs/what-is-twg-cli/
- TWG setup: https://support.atlassian.com/organization-administration/docs/get-started-with-twg-cli/
- TWG auth: https://support.atlassian.com/organization-administration/docs/how-does-teamwork-graph-cli-authentication-work/
- Community JiraCLI: https://github.com/ankitpokhrel/jira-cli

## Forge

- Fetch API: https://developer.atlassian.com/platform/forge/runtime-reference/fetch-api/

## Automation

- Jira actions: https://support.atlassian.com/cloud-automation/docs/jira-automation-actions/
- Third-party/incoming webhook: https://support.atlassian.com/cloud-automation/docs/what-third-party-applications-can-i-integrate-with/
- Confluence actions: https://support.atlassian.com/cloud-automation/docs/actions-in-confluence-automation/

## Data Lake / Analytics

- Data Lake: https://support.atlassian.com/analytics/docs/what-is-the-atlassian-data-lake/
- Data model: https://support.atlassian.com/analytics/docs/understand-the-data-model-of-the-atlassian-data-lake/
- Data shares: https://support.atlassian.com/analytics/docs/what-is-a-data-share/
- Create a share: https://support.atlassian.com/analytics/docs/create-a-data-share/
- Share model: https://support.atlassian.com/analytics/docs/understand-the-data-model-of-data-shares/

## Data Center

- Jira REST: https://developer.atlassian.com/server/jira/platform/rest/
- Jira Basic auth: https://developer.atlassian.com/server/jira/platform/basic-authentication/
- Confluence REST: https://developer.atlassian.com/server/confluence/rest/

---

# 31. How this report handles “every API”

Atlassian's public REST surfaces contain hundreds of operations and evolve continuously. A static pasted endpoint list becomes stale quickly. This report therefore documents them at two levels:

1. **Every current public resource group** for Jira Platform v3, Jira Software, JSM and Confluence v2 is enumerated above.
2. **Official OpenAPI files** are linked as the operation-level source of truth.

For engineering documentation, generate an internal endpoint table/client from OpenAPI rather than manually copying hundreds of routes.

Example:

```bash
curl -L \
  "https://dac-static.atlassian.com/cloud/jira/platform/swagger-v3.v3.json" \
  -o jira-openapi.json

jq -r '.paths | keys[]' jira-openapi.json
```

Confluence:

```bash
curl -L \
  "https://dac-static.atlassian.com/cloud/confluence/openapi-v2.v3.json" \
  -o confluence-openapi.json

jq -r '.paths | keys[]' confluence-openapi.json
```

If Atlassian changes a static URL, open the REST reference and use its current **OpenAPI** link.

---

# 32. Recommended stack for a custom internal agent

For an agent that needs Jira issues/comments/sprints and Confluence pages:

```text
Primary agent interface: Atlassian MCP v2
Specialized deterministic fallback: REST
Low-latency change ingestion: webhooks/events
Bulk BI/history: Data Lake/data share (when available)
Human/UI fallback: integrated browser
Developer shell: ACLI or TWG CLI
```

Start with read-only MCP groups:

```text
read_jira
search_jira
read_confluence
search_confluence
```

Only enable write/delete/manage after you have an explicit workflow and confirmation policy.

---

# 33. Freshness and source-quality notes

- Atlassian Developer and Atlassian Support documentation were treated as authoritative.
- The community `jira-cli` is explicitly third-party.
- Research reflects documentation available on **1 October 2026**.
- Endpoint deprecations, OAuth scopes, MCP tools and Rovo-credit behavior can change; monitor Atlassian changelogs and regenerate your OpenAPI inventory in CI.
- The current Jira docs mark legacy `/rest/api/3/search` JQL endpoints as being removed; use `/rest/api/3/search/jql` for new work.
- Confluence v2 is the preferred newer REST surface for supported content resources, while CQL search remains important in v1-style search endpoints.

---

## End of report
