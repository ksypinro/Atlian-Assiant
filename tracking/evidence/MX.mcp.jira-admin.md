# Evidence: MX.mcp.jira-admin - Jira: create project / workflows / screens on mcp

- submitted by: claude
- at: 2026-10-02T18:21:16Z
- commit: n/a

## Checks

### `pytest tests/contract -k 'jira_admin and mcp' -q` -> exit 0
```
.                                                                        [100%]
1 passed, 230 deselected in 0.56s
```

## Files changed

- atlassian_client/agent/docs/AGENT_GUIDE.md
- atlassian_client/agent/docs/llms.txt
- atlassian_client/agent/tools.py
- atlassian_client/backends/mcp/__init__.py
- atlassian_client/backends/mcp/backend.py
- atlassian_client/backends/rest_cloud/__init__.py
- atlassian_client/backends/rest_cloud/backend.py
- atlassian_client/facade/_generated_agile.py
- atlassian_client/facade/_generated_confluence.py
- atlassian_client/facade/_generated_graph.py
- atlassian_client/facade/_generated_jira.py
- atlassian_client/facade/_generated_jsm.py
- atlassian_client/facade/_generated_raw.py
- atlassian_client/facade/confluence.py
- atlassian_client/facade/jira.py
- atlassian_client/policy.py
- atlassian_client/protocols/__init__.py
- atlassian_client/protocols/confluence.py
- atlassian_client/testing/fake.py
- atlassian_client/testing/mcp_fake.py
- atlassian_client/testing/scenario.py
- atlassian_client/testing/server.py
- docs/operations.md
- tests/agent/conftest.py
- tests/agent/test_p1_agent_001.py
- tests/agent/test_p1_agent_002.py
- tests/agent/test_tools.py
- tests/backends/test_rest_factory.py
- tests/contract/test_mcp_matrix.py
- tests/core/test_describe.py
- tests/core/test_p0_core_003.py
- tests/core/test_p0_core_016.py
- tests/mcp/test_p1_mcp_001.py
- tests/qa/test_p0_qa_001.py
- tools/docgen.py
- tools/gen_facade.py
- tools/track/engine.py
- tracking/environment.yaml
- atlassian_client/backends/mcp/write.py
- atlassian_client/backends/rest_cloud/agile_write.py
- atlassian_client/backends/rest_cloud/bodies.py
- atlassian_client/backends/rest_cloud/confluence_write.py
- atlassian_client/backends/rest_cloud/jira_write.py
- atlassian_client/backends/rest_cloud/jsm_write.py
- tests/agent/test_p2_agent_001.py
- tests/contract/test_confluencecontentdeleter.py
- tests/contract/test_confluencecontentwriter.py
- tests/contract/test_confluencepermissionwriter.py
- tests/contract/test_confluencespacewriter.py
- tests/contract/test_jiraadmin.py
- tests/contract/test_jiraagilewriter.py
- tests/contract/test_jiraissuedeleter.py
- tests/contract/test_jiraissuewriter.py
- tests/contract/test_jiraprojectwriter.py
- tests/contract/test_jsmwriter.py
- tests/contract/test_mcp_writes.py
- tests/contract/test_teamworkgraphwriter.py
- tests/rest/test_p2_rest_001.py
- tests/rest/test_p2_rest_002.py
- tests/routing/
- tests/security/test_security_p2.py
- tracking/evidence/MX.mcp.agile-write.md
- tracking/evidence/MX.mcp.conf-content-write.md
- tracking/evidence/MX.mcp.conf-special.md
- tracking/state/MX.mcp.agile-write.yaml
- tracking/state/MX.mcp.conf-content-write.yaml
- tracking/state/MX.mcp.conf-special.yaml
- tracking/state/MX.mcp.jira-admin.yaml

## Outside declared deliverables (reviewer: check)

- atlassian_client/agent/docs/AGENT_GUIDE.md
- atlassian_client/agent/docs/llms.txt
- atlassian_client/agent/tools.py
- atlassian_client/backends/rest_cloud/__init__.py
- atlassian_client/backends/rest_cloud/backend.py
- atlassian_client/facade/_generated_agile.py
- atlassian_client/facade/_generated_confluence.py
- atlassian_client/facade/_generated_graph.py
- atlassian_client/facade/_generated_jira.py
- atlassian_client/facade/_generated_jsm.py
- atlassian_client/facade/_generated_raw.py
- atlassian_client/facade/confluence.py
- atlassian_client/facade/jira.py
- atlassian_client/policy.py
- atlassian_client/protocols/__init__.py
- atlassian_client/protocols/confluence.py
- atlassian_client/testing/fake.py
- atlassian_client/testing/mcp_fake.py
- atlassian_client/testing/scenario.py
- atlassian_client/testing/server.py
- docs/operations.md
- tests/agent/conftest.py
- tests/agent/test_p1_agent_001.py
- tests/agent/test_p1_agent_002.py
- tests/agent/test_tools.py
- tests/backends/test_rest_factory.py
- tests/contract/test_mcp_matrix.py
- tests/core/test_describe.py
- tests/core/test_p0_core_003.py
- tests/core/test_p0_core_016.py
- tests/mcp/test_p1_mcp_001.py
- tests/qa/test_p0_qa_001.py
- tools/docgen.py
- tools/gen_facade.py
- tools/track/engine.py
- tracking/environment.yaml
- atlassian_client/backends/rest_cloud/agile_write.py
- atlassian_client/backends/rest_cloud/bodies.py
- atlassian_client/backends/rest_cloud/confluence_write.py
- atlassian_client/backends/rest_cloud/jira_write.py
- atlassian_client/backends/rest_cloud/jsm_write.py
- tests/agent/test_p2_agent_001.py
- tests/contract/test_confluencecontentdeleter.py
- tests/contract/test_confluencecontentwriter.py
- tests/contract/test_confluencepermissionwriter.py
- tests/contract/test_confluencespacewriter.py
- tests/contract/test_jiraadmin.py
- tests/contract/test_jiraagilewriter.py
- tests/contract/test_jiraissuedeleter.py
- tests/contract/test_jiraissuewriter.py
- tests/contract/test_jiraprojectwriter.py
- tests/contract/test_jsmwriter.py
- tests/contract/test_mcp_writes.py
- tests/contract/test_teamworkgraphwriter.py
- tests/rest/test_p2_rest_001.py
- tests/rest/test_p2_rest_002.py
- tests/routing/
- tests/security/test_security_p2.py
- tracking/evidence/MX.mcp.agile-write.md
- tracking/evidence/MX.mcp.conf-content-write.md
- tracking/evidence/MX.mcp.conf-special.md
- tracking/state/MX.mcp.agile-write.yaml
- tracking/state/MX.mcp.conf-content-write.yaml
- tracking/state/MX.mcp.conf-special.yaml
- tracking/state/MX.mcp.jira-admin.yaml
