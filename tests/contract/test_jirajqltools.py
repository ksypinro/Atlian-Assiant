import pytest

from atlassian_client.protocols import JiraJqlTools

pytestmark = [pytest.mark.layer_01]


@pytest.mark.anyio
async def test_jira_jql_tools_parse_flags_invalid_queries(impl):
    good, bad = await impl(JiraJqlTools).parse_jql(["project = PROJ", "BROKEN ((("])
    assert good.valid and not good.errors
    assert not bad.valid and bad.errors


@pytest.mark.anyio
async def test_jira_jql_tools_sanitize_autocomplete_match_expression(impl):
    r = impl(JiraJqlTools)
    assert await r.sanitize_jql(["assignee = ann"]) == ["assignee = acc-ann"]
    assert "visibleFieldNames" in await r.autocomplete()
    assert (await r.autocomplete(field_name="status"))["results"][0]["value"] == "Done"
    assert await r.match_jql([10001], ["project = PROJ"]) == {"project = PROJ": [10001]}
    assert await r.evaluate_expression("1 + 1", {}) == 2
