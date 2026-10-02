import pytest

from atlassian_client.core.errors import NotFoundError
from atlassian_client.protocols import JiraProjectReader

pytestmark = [pytest.mark.layer_01]


@pytest.mark.anyio
async def test_jira_projects_list_get_components_versions(impl):
    r = impl(JiraProjectReader)
    page = await r.list_projects()
    assert [p.key for p in page.items] == ["PROJ", "OPS"]
    assert [p.key for p in (await r.list_projects(query="oper")).items] == ["OPS"]
    assert (await r.get_project("PROJ")).name == "Project"
    assert [c.name for c in await r.list_components("PROJ")] == ["API"]
    v = (await r.list_versions("PROJ"))[0]
    assert (v.name, v.released) == ("1.0", True)


@pytest.mark.anyio
async def test_jira_projects_pagination(impl):
    first = await impl(JiraProjectReader).list_projects(limit=1)
    assert len(first.items) == 1 and first.next_cursor
    second = await impl(JiraProjectReader).list_projects(limit=1, cursor=first.next_cursor)
    assert second.items[0].key == "OPS" and second.next_cursor is None
