# Atlian-Assiant

**One Python client for every way of reading, writing and watching Jira and Confluence.**

Cloud and Data Center REST, the Atlassian remote MCP server, the GraphQL gateway, the `acli` / `twg` / `jira` command-line tools, a
signed-in browser, Data Lake shares and webhooks / Forge / polling for events. A per-operation router picks the backend that can serve each
call (and falls back when one cannot), one access policy guards all of them, and the same operations are available as LLM tools, an MCP server
and a CLI.

> **Status: alpha (`0.1.0a1`).** Reads are verified against a live Jira + Confluence Cloud site; writes and several backends are tested only
> against built-in fakes so far. See [what is verified](#what-is-verified). APIs may change before 1.0.

> Atlian-Assiant is an independent open-source project. It is **not affiliated with, endorsed by or sponsored by Atlassian**. Jira, Confluence
> and related names are trademarks of Atlassian Pty Ltd (see [NOTICE](NOTICE)).

## Install

```bash
pip install atlian-assiant                 # core: REST backends
pip install 'atlian-assiant[mcp]'          # + Atlassian remote MCP server
pip install 'atlian-assiant[browser]'      # + Playwright browser backend
```

Other extras: `datalake`, `duckdb`, `db`, `postgres`, `chroma`, `weaviate`, `pinecone`, `aws`, `azure`, `gcp`, `server`, `slack`, `kafka`, ... (see
[`pyproject.toml`](pyproject.toml)). Python 3.11+.

## Quick start

```bash
export ATLASSIAN_SITE=your-site.atlassian.net
export ATLASSIAN_EMAIL=you@example.com
export ATLASSIAN_API_TOKEN=...             # https://id.atlassian.com/manage-profile/security/api-tokens
```

```python
from atlian_assiant import AtlassianClient

with AtlassianClient.from_env() as atl:
    print(atl.describe())                              # what is configured, what is not, and why
    issue = atl.jira.issues.get("PROJ-123")
    for hit in atl.jira.issues.search("project = PROJ AND updated >= -1d", limit=20).items:
        print(hit.key, hit.summary)
    page = atl.confluence.content.get("123456")        # body as Markdown
    ops = atl.index.discover("issue watchers")         # every operation, including the generated OpenAPI tier
```

```bash
atlian-assiant describe
atlian-assiant jira issue search "project = PROJ AND updated >= -1d" --limit 20
atlian-assiant mcp serve --preset agent --access read,search      # every allowed tool over MCP (stdio)
```

The client is **read-only by default**. Writes need `access=...write` and are previewed with `dry_run=True`; destructive actions always ask
for confirmation.

## What is verified

Checked live against a Jira + Confluence Cloud site (October 2026):

| Way of working | Status |
|---|---|
| REST (Cloud) | ✅ reads verified live |
| Atlassian remote MCP server (v2) | ✅ reads verified live (scoped API token; the org admin must allow API-token auth) |
| GraphQL gateway | ✅ 13 stored read queries verified live and checked against the recorded schema |
| `acli` 1.3.39 | ✅ command map rebuilt from the real `--help`; reads verified live |
| `twg` 1.3.3 | ✅ Confluence reads verified live (Rovo search / graph context not run: they consume credits) |
| Writes (all backends) | ⚠️ tested against fakes only |
| Events: webhooks, Forge, polling, SQS, JEC, Slack, ... | ⚠️ tested against fakes only |
| `jira` CLI, browser, Data Center REST / database, Data Lake, org admin | ⚠️ tested against fakes only |

Anything a backend cannot do is reported as *unsupported*, and the router falls back to REST or MCP. The full operation × backend matrix is in
[`docs/architecture_design.md`](docs/architecture_design.md) (section 11) and [`docs/operation_coverage.md`](docs/operation_coverage.md).

## Documentation

* [Architecture overview with diagrams](architecture.md) · [Full design specification](docs/architecture_design.md) · [Operations](docs/operations.md) · [Event delivery methods](docs/delivery_methods.md)
* [For LLM agents](atlian_assiant/agent/docs/AGENT_GUIDE.md) · [Releasing and compatibility](docs/RELEASING.md)
* Research behind the design: [data access](docs/research/atlassian_jira_confluence_data_access_research.md) ·
  [events](docs/research/atlassian_webhook_event_stream_private_delivery_research.md) ·
  [combined flow](docs/research/combined_atlassian_integration_flow.md)
* Slack listener example: [`deploy/slack/`](deploy/slack/README.md)

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest -q                          # ~2,500 tests, no credentials needed (fakes)
mypy atlian_assiant && ruff check atlian_assiant
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Please report security problems privately as described in [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) © 2026 Kazi Samin Yeaser
