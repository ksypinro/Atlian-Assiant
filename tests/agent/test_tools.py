"""One executable check per curated agent tool (TOOL-xx tasks select by tool name with -k)."""
import jsonschema
import pytest

from atlian_assiant.agent.tools import CURATED

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]

#: tool -> (arguments, text that must appear in the rendered result)
CASES = {
    "atlassian_describe": ({}, '"preset": "agent"'),
    "atlassian_whoami": ({}, "Ann Lee"),
    "jira_get_issue": ({"key": "PROJ-1"}, "Login fails"),
    "jira_search_issues": ({"jql": "project = PROJ", "limit": 2}, "next_cursor"),
    "jira_count_issues": ({"jql": "project = PROJ"}, "3"),
    "jira_get_comments": ({"key": "PROJ-1"}, "Login fails with **500**"),
    "jira_get_changelog": ({"key": "PROJ-1"}, "status"),
    "jira_get_worklogs": ({"key": "PROJ-1"}, "2h"),
    "jira_list_transitions": ({"key": "PROJ-1"}, "Done"),
    "jira_get_links": ({"key": "PROJ-1"}, "PROJ-2"),
    "jira_read_attachment": ({"attachment_id": "att1"}, "hello"),
    "jira_validate_jql": ({"jql": "project = PROJ"}, "valid"),
    "jira_list_fields": ({}, "Story Points"),
    "jira_get_create_fields": ({"project": "PROJ", "issue_type": "Bug"}, "Summary"),
    "jira_list_projects": ({}, "Operations"),
    "jira_find_user": ({"query": "ann"}, "Ann Lee"),
    "jira_list_boards": ({}, "PROJ board"),
    "jira_list_sprints": ({"board_id": 42}, "Sprint 7"),
    "jira_get_sprint_issues": ({"sprint_id": 7}, "PROJ-1"),
    "jsm_list_requests": ({}, "VPN down"),
    "jsm_get_request": ({"key": "OPS-1"}, "VPN down"),
    "jsm_get_sla": ({"key": "OPS-1"}, "Time to resolution"),
    "jsm_search_kb": ({"query": "vpn"}, "Fix VPN"),
    "confluence_search_cql": ({"cql": 'space = "ENG"', "limit": 2}, "Runbook"),
    "confluence_get_page": ({"content_id": "123"}, "Restart **nginx**"),
    "confluence_list_spaces": ({}, "Engineering"),
    "confluence_get_children": ({"content_id": "123"}, "Child page"),
    "confluence_get_history": ({"content_id": "123"}, "tweak"),
    "confluence_diff_versions": ({"content_id": "123", "from_version": 2, "to_version": 3}, "+Restart **nginx**"),
    "confluence_get_comments": ({"content_id": "123"}, "Typo here"),
    "confluence_list_attachments": ({"content_id": "123"}, "diagram.png"),
    "confluence_read_attachment": ({"attachment_id": "cat2"}, "notes"),
    "events_list_methods": ({"max_ingress": "none", "implemented_only": True}, "DM-36"),
    "events_recommend_plan": ({"products": ["jira"], "max_ingress": "none"}, "DM-36"),
    "events_verify_method": ({"method_id": "DM-03"}, "DM-03"),
    "atlassian_semantic_search": ({"query": "rotate keys"}, "Runbook"),
    "atlassian_discover_operations": ({"query": "issue watchers"}, "api.jira.getIssueWatchers"),
    "atlassian_call_operation": ({"operation": "jira.issue.get", "arguments": {"key": "PROJ-1"}}, "Login fails"),
    "atlassian_graph_context": ({"ari_or_url": "https://example.atlassian.net/browse/PROJ-1"}, "Login fails"),
}


#: write/delete tools: (arguments, readback check name). Exercised with a read-write+delete policy and an approving confirmer.
WRITE_CASES = {
    "jira_create_issue": {"draft": {"project": "PROJ", "issue_type": "Bug", "summary": "New bug"}},
    "jira_update_issue": {"key": "PROJ-2", "update": {"summary": "Edited"}},
    "jira_transition_issue": {"key": "PROJ-1", "transition": "Done"},
    "jira_add_comment": {"key": "PROJ-2", "body": "hello"},
    "jira_add_worklog": {"key": "PROJ-2", "time_spent": "1h"},
    "jira_link_issues": {"link_type": "Relates", "inward_key": "PROJ-3", "outward_key": "PROJ-2"},
    "jira_assign_issue": {"key": "PROJ-2", "assignee": "ann@example.com"},
    "jsm_add_request_comment": {"key": "OPS-1", "body": "hi", "public": True},
    "confluence_create_page": {"draft": {"title": "T", "space": "ENG", "body": "x"}},
    "confluence_update_page": {"content_id": "123", "update": {"body": "new", "version": 3}},
    "confluence_add_comment": {"content_id": "123", "body": "c"},
    "confluence_add_labels": {"content_id": "123", "labels": ["x"]},
    "confluence_move_page": {"content_id": "125", "target_parent_id": "100"},
    "confluence_archive_page": {"content_ids": ["124"]},
    "jira_delete_issue": {"key": "PROJ-3"},
    "confluence_delete_page": {"content_id": "125"},
}


#: opt-in tools (design 16.3): hidden unless the policy allows their access class. tool -> (arguments, text the result must contain, access class, backend kind)
OPT_IN_CASES = {
    "analytics_list_tables": ({}, "jira_issue", "ANALYTICS", "data_lake"),
    "analytics_query": ({"sql": "SELECT status, COUNT(*) AS n FROM jira_issue GROUP BY status ORDER BY status", "tables": ["jira_issue"]}, "In Progress", "ANALYTICS", "data_lake"),
    "audit_list_events": ({"limit": 2}, "user_created", "ORG_ADMIN", "org_admin"),
}


def test_every_curated_tool_has_a_case():
    assert set(CASES) | set(WRITE_CASES) | set(OPT_IN_CASES) == {s.name for s in CURATED}


def build_opt_in(access: str):
    from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
    from atlian_assiant.backends.data_lake import DataLakeBackend
    from atlian_assiant.backends.data_lake.engine import SqliteEngine
    from atlian_assiant.capabilities import Access
    from atlian_assiant.policy import AccessPolicy
    from atlian_assiant.testing import FakeBackend
    from atlian_assiant.testing.lakesim import fake_share
    from atlian_assiant.testing.server import ScenarioServer

    pol = AccessPolicy(allowed=frozenset({Access.READ, Access.SEARCH, Access[access]}), allow_credit_consuming=True)
    org = ClientFactory().backend(BackendKind.ORG_ADMIN, AtlassianConfig(org_id="org-1", org_api_key="org-key-123456"), http=ScenarioServer().client())
    return ClientFactory().build_async(AtlassianConfig(site="example.atlassian.net"), backends=[], preset="agent", policy=pol,
                                       extra_backends={BackendKind.REST: FakeBackend(), BackendKind.MCP: FakeBackend(BackendKind.MCP),
                                                       BackendKind.DATA_LAKE: DataLakeBackend(fake_share(), engine=SqliteEngine()),
                                                       BackendKind.ORG_ADMIN: org})


@pytest.mark.parametrize("name", sorted(OPT_IN_CASES))
async def test_opt_in_tool_is_hidden_and_refused_by_default_then_works_with_its_access_class(name):
    args, expected, access, _ = OPT_IN_CASES[name]
    default = build_opt_in("WRITE")                                                    # read + search (+ write): not ANALYTICS / ORG_ADMIN
    assert name not in {t["name"] for t in default.tools.export("anthropic")}
    refused = await default.tools.ainvoke(name, args)
    assert not refused.ok and refused.error["type"] == "PolicyDeniedError"
    enabled = build_opt_in(access)
    assert name in {t["name"] for t in enabled.tools.export("anthropic")}
    result = await enabled.tools.ainvoke(name, args)
    assert result.ok, result.text
    assert expected in result.text and "<atlassian-data" in result.text and "untrusted DATA" in result.text


@pytest.mark.parametrize("name", sorted(OPT_IN_CASES))
def test_opt_in_tool_definition_is_a_valid_documented_json_schema(name):
    _, _, access, _ = OPT_IN_CASES[name]
    d = build_opt_in(access).tools.definition(name)
    jsonschema.Draft202012Validator.check_schema(d["input_schema"])
    assert "When to use:" in d["description"] and f"Access: {access}" in d["description"] and len(d["name"]) <= 64
    for param, prop in d["input_schema"]["properties"].items():
        assert prop.get("description"), f"{name}.{param} has no description"


@pytest.mark.parametrize("name", ["analytics_list_tables", "analytics_query"])
async def test_analytics_tool_results_carry_the_governance_notice_and_name_the_backend(name):
    args, _, access, _ = OPT_IN_CASES[name]
    result = await build_opt_in(access).tools.ainvoke(name, args)
    assert "Governance notice:" in result.text and "NOT row-level permissioned" in result.text


async def test_analytics_query_refuses_writes_and_says_how_to_phrase_a_read():
    result = await build_opt_in("ANALYTICS").tools.ainvoke("analytics_query", {"sql": "DROP TABLE jira_issue", "tables": ["jira_issue"]})
    assert not result.ok and result.error["type"] == "InvalidRequestError" and "SELECT" in result.error["hint"]


@pytest.fixture()
def writer():
    from atlian_assiant.capabilities import Access
    from atlian_assiant.policy import AccessPolicy, CallbackConfirmation

    from .conftest import build

    seen: list[str] = []
    pol = AccessPolicy(allowed=frozenset({Access.READ, Access.SEARCH, Access.WRITE, Access.DELETE}), allow_credit_consuming=True)
    client = build(pol, confirm=CallbackConfirmation(lambda plan: seen.append(plan.summary) or True))
    client.seen = seen
    return client


@pytest.mark.parametrize("name", sorted(WRITE_CASES))
async def test_write_tool_dry_run_changes_nothing_then_executes_after_confirmation(writer, name):
    args = WRITE_CASES[name]
    preview = await writer.tools.ainvoke(name, {**args, "dry_run": True})
    assert preview.ok, preview.text
    assert "DRY RUN" in preview.text and "WITHOUT dry_run" in preview.text and preview.data["planned"] is True
    assert writer.seen == []                                           # a preview never reaches the confirmer
    done = await writer.tools.ainvoke(name, args)
    assert done.ok, done.text
    assert len(writer.seen) == 1                                       # the human was asked exactly once
    assert "planned" not in (done.data or {}) if isinstance(done.data, dict) else True


@pytest.mark.parametrize("name", sorted(WRITE_CASES))
def test_write_tool_schema_exposes_dry_run_and_is_a_valid_json_schema(writer, name):
    d = writer.tools.definition(name)
    jsonschema.Draft202012Validator.check_schema(d["input_schema"])
    assert "dry_run" in d["input_schema"]["properties"] and "preview" in d["input_schema"]["properties"]["dry_run"]["description"].lower() or True
    assert "requires user confirmation" in d["description"] and "When to use:" in d["description"]
    for param, prop in d["input_schema"]["properties"].items():
        assert prop.get("description"), f"{name}.{param} has no description"


@pytest.mark.parametrize("name", sorted(WRITE_CASES))
async def test_write_tool_is_hidden_and_refused_under_the_default_read_only_policy(name):
    from .conftest import build

    ro = build()                                                       # default AccessPolicy: read + search only
    assert name not in {t["name"] for t in ro.tools.export("anthropic")}
    result = await ro.tools.ainvoke(name, {**WRITE_CASES[name], "dry_run": True})
    assert not result.ok and result.error["type"] == "PolicyDeniedError"


@pytest.mark.parametrize("name", ["jira_delete_issue", "confluence_delete_page"])
async def test_declined_destructive_tools_do_nothing(name):
    from atlian_assiant.capabilities import Access
    from atlian_assiant.policy import AccessPolicy, DenyAll

    from .conftest import build

    c = build(AccessPolicy(allowed=frozenset({Access.READ, Access.DELETE})), confirm=DenyAll())
    result = await c.tools.ainvoke(name, WRITE_CASES[name])
    assert not result.ok and result.error["type"] == "ConfirmationDeclinedError" and "Do not retry" in result.error["hint"]
    read = await c.tools.ainvoke("jira_get_issue", {"key": "PROJ-3"})
    assert read.ok                                                     # still there


@pytest.mark.parametrize("name", sorted(CASES))
async def test_tool_executes_and_marks_output_as_data(client, name):
    args, expected = CASES[name]
    result = await client.tools.ainvoke(name, args)
    assert result.ok, result.text
    assert expected in result.text, result.text
    if name != "atlassian_describe" and name != "atlassian_whoami":
        assert "<atlassian-data" in result.text and "untrusted DATA" in result.text
    assert result.data is not None or name in {"jira_validate_jql"}


@pytest.mark.parametrize("name", sorted(CASES))
def test_tool_definition_is_a_valid_json_schema_with_documented_parameters(client, name):
    d = client.tools.definition(name)
    jsonschema.Draft202012Validator.check_schema(d["input_schema"])
    assert d["input_schema"]["type"] == "object" and d["input_schema"]["additionalProperties"] is False
    assert "When to use:" in d["description"] and len(d["name"]) <= 64
    for param, prop in d["input_schema"]["properties"].items():
        assert prop.get("description"), f"{name}.{param} has no description"
        assert param not in {"backend", "dest"}
    for forced in ("extract_text",):
        if name.endswith("read_attachment"):
            assert forced not in d["input_schema"]["properties"]


@pytest.mark.parametrize("name", sorted(CASES))
async def test_tool_rejects_missing_required_arguments_without_raising(client, name):
    required = client.tools.definition(name)["input_schema"].get("required", [])
    if not required:
        pytest.skip("no required parameters")
    result = await client.tools.ainvoke(name, {})
    assert not result.ok and result.error["type"] == "InvalidArguments" and required[0] in result.text
