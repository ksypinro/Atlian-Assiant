import pytest

from atlassian_client.core.errors import NotFoundError
from atlassian_client.protocols import JiraMetadataReader

pytestmark = [pytest.mark.layer_01]


@pytest.mark.anyio
async def test_jira_fields_meta_lists_fields_and_create_metadata(impl):
    r = impl(JiraMetadataReader)
    fields = await r.list_fields()
    assert {f.id: f.custom for f in fields} == {"summary": False, "customfield_10016": True}
    assert [t.name for t in await r.list_issue_types()] == ["Bug", "Task"]
    create = await r.get_create_fields("PROJ", "Bug")
    assert [(f.id, f.required) for f in create] == [("summary", True), ("customfield_10016", False)]
    assert (await r.server_info()).deployment_type == "Cloud"


@pytest.mark.anyio
async def test_jira_fields_meta_statuses_priorities_link_types(impl):
    r = impl(JiraMetadataReader)
    assert [s.name for s in await r.list_statuses()] == ["To Do", "Done"]
    assert await r.list_priorities() == ["High", "Low"]
    assert await r.list_link_types() == ["Blocks", "Relates"]


@pytest.mark.anyio
async def test_jira_fields_meta_unknown_issue_type_lists_valid_ones(impl):
    with pytest.raises(NotFoundError) as exc:
        await impl(JiraMetadataReader).get_create_fields("PROJ", "Epic")
    assert "Epic" in str(exc.value)


@pytest.mark.anyio
async def test_jira_filters_dashboards_list(impl):
    r = impl(JiraMetadataReader)
    f = (await r.list_filters()).items[0]
    assert (f.name, f.jql) == ("My open issues", "assignee = currentUser() AND resolution = EMPTY")
    assert (await r.list_filters(query="nothing")).items == []
    assert (await r.list_dashboards()).items[0].name == "Team"
