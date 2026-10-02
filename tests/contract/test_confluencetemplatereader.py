import pytest

from atlassian_client.core.errors import NotFoundError
from atlassian_client.protocols import ConfluenceTemplateReader

pytestmark = [pytest.mark.layer_01]


@pytest.mark.anyio
async def test_conf_tasks_templates_reactions_templates_list_and_get(impl):
    r = impl(ConfluenceTemplateReader)
    assert [t.id for t in await r.list_templates()] == ["tpl1"]
    assert await r.list_templates(space_key="OPS") == []
    assert (await r.get_template("tpl1")).name == "Runbook template"
    with pytest.raises(NotFoundError):
        await r.get_template("nope")
