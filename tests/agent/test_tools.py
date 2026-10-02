"""One executable check per curated agent tool (TOOL-xx tasks select by tool name with -k)."""
import jsonschema
import pytest

from atlassian_client.agent.tools import CURATED

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
    "atlassian_semantic_search": ({"query": "rotate keys"}, "Runbook"),
    "atlassian_graph_context": ({"ari_or_url": "https://example.atlassian.net/browse/PROJ-1"}, "Login fails"),
}


def test_every_curated_tool_has_a_case():
    assert set(CASES) == {s.name for s in CURATED}


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
