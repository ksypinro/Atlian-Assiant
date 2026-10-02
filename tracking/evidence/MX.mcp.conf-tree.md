# Evidence: MX.mcp.conf-tree - Confluence: children / ancestors / descendants on mcp (partial)

- submitted by: claude
- at: 2026-10-02T17:59:06Z
- commit: n/a

## Checks

### `pytest tests/contract -k 'conf_tree and mcp' -q` -> exit 0
```
.                                                                        [100%]
1 passed, 128 deselected in 0.44s
```

## Files changed

- TRACKING.md
- atlassian_client/backends/__init__.py
- atlassian_client/backends/mcp/__init__.py
- atlassian_client/core/telemetry.py
- atlassian_client/facade/_generated_agile.py
- atlassian_client/facade/_generated_confluence.py
- atlassian_client/facade/_generated_jira.py
- atlassian_client/facade/_generated_jsm.py
- atlassian_client/facade/_generated_raw.py
- atlassian_client/facade/client.py
- atlassian_client/facade/sync.py
- atlassian_client/factory.py
- atlassian_client/policy.py
- atlassian_client/routing.py
- atlassian_client/testing/fake.py
- pyproject.toml
- scripts/check.sh
- tests/backends/test_rest_factory.py
- tests/core/test_p0_core_014.py
- tests/core/test_p0_core_016.py
- tests/qa/test_p0_qa_001.py
- tools/docgen.py
- tools/gen_facade.py
- tracking/definitions/generated/TOOL-55.yaml
- tracking/definitions/generated/TOOL-56.yaml
- tracking/definitions/seed_hints.yaml
- atlassian_client/agent/docs/
- atlassian_client/agent/tools.py
- atlassian_client/backends/mcp/backend.py
- atlassian_client/backends/mcp/session.py
- atlassian_client/facade/_generated_graph.py
- atlassian_client/testing/mcp_fake.py
- docs/
- tests/agent/
- tests/backends/test_mcp_factory.py
- tests/contract/mcp_helpers.py
- tests/contract/test_mcp_matrix.py
- tests/contract/test_semanticsearcher.py
- tests/contract/test_teamworkgraphreader.py
- tests/docs/test_p1_docs_001.py
- tests/live/
- tests/mcp/
- tests/security/test_security_p1.py
- tracking/evidence/BE.mcp.md
- tracking/evidence/MX.mcp.agile-read.md
- tracking/evidence/MX.mcp.conf-comments-attachments-labels.md
- tracking/evidence/MX.mcp.conf-content-get.md
- tracking/evidence/MX.mcp.conf-cql-search.md
- tracking/evidence/MX.mcp.conf-macros.md
- tracking/evidence/MX.mcp.conf-permissions.md
- tracking/evidence/MX.mcp.conf-tasks-templates-reactions.md
- tracking/evidence/P1.AGENT.001.md
- tracking/evidence/P1.AGENT.002.md
- tracking/state/BE.mcp.yaml
- tracking/state/MX.mcp.agile-read.yaml
- tracking/state/MX.mcp.conf-comments-attachments-labels.yaml
- tracking/state/MX.mcp.conf-content-get.yaml
- tracking/state/MX.mcp.conf-cql-search.yaml
- tracking/state/MX.mcp.conf-macros.yaml
- tracking/state/MX.mcp.conf-permissions.yaml
- tracking/state/MX.mcp.conf-tasks-templates-reactions.yaml
- tracking/state/MX.mcp.conf-tree.yaml
- tracking/state/P1.AGENT.001.yaml
- tracking/state/P1.AGENT.002.yaml

## Outside declared deliverables (reviewer: check)

- atlassian_client/backends/__init__.py
- atlassian_client/core/telemetry.py
- atlassian_client/facade/_generated_agile.py
- atlassian_client/facade/_generated_confluence.py
- atlassian_client/facade/_generated_jira.py
- atlassian_client/facade/_generated_jsm.py
- atlassian_client/facade/_generated_raw.py
- atlassian_client/facade/client.py
- atlassian_client/facade/sync.py
- atlassian_client/factory.py
- atlassian_client/policy.py
- atlassian_client/routing.py
- atlassian_client/testing/fake.py
- pyproject.toml
- scripts/check.sh
- tests/backends/test_rest_factory.py
- tests/core/test_p0_core_014.py
- tests/core/test_p0_core_016.py
- tests/qa/test_p0_qa_001.py
- tools/docgen.py
- tools/gen_facade.py
- tracking/definitions/generated/TOOL-55.yaml
- tracking/definitions/generated/TOOL-56.yaml
- tracking/definitions/seed_hints.yaml
- atlassian_client/agent/docs/
- atlassian_client/agent/tools.py
- atlassian_client/facade/_generated_graph.py
- atlassian_client/testing/mcp_fake.py
- docs/
- tests/agent/
- tests/backends/test_mcp_factory.py
- tests/contract/mcp_helpers.py
- tests/contract/test_mcp_matrix.py
- tests/contract/test_semanticsearcher.py
- tests/contract/test_teamworkgraphreader.py
- tests/docs/test_p1_docs_001.py
- tests/live/
- tests/mcp/
- tests/security/test_security_p1.py
- tracking/evidence/BE.mcp.md
- tracking/evidence/MX.mcp.agile-read.md
- tracking/evidence/MX.mcp.conf-comments-attachments-labels.md
- tracking/evidence/MX.mcp.conf-content-get.md
- tracking/evidence/MX.mcp.conf-cql-search.md
- tracking/evidence/MX.mcp.conf-macros.md
- tracking/evidence/MX.mcp.conf-permissions.md
- tracking/evidence/MX.mcp.conf-tasks-templates-reactions.md
- tracking/evidence/P1.AGENT.001.md
- tracking/evidence/P1.AGENT.002.md
- tracking/state/BE.mcp.yaml
- tracking/state/MX.mcp.agile-read.yaml
- tracking/state/MX.mcp.conf-comments-attachments-labels.yaml
- tracking/state/MX.mcp.conf-content-get.yaml
- tracking/state/MX.mcp.conf-cql-search.yaml
- tracking/state/MX.mcp.conf-macros.yaml
- tracking/state/MX.mcp.conf-permissions.yaml
- tracking/state/MX.mcp.conf-tasks-templates-reactions.yaml
- tracking/state/MX.mcp.conf-tree.yaml
- tracking/state/P1.AGENT.001.yaml
- tracking/state/P1.AGENT.002.yaml
