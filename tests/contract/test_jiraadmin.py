import pytest

from atlian_assiant.protocols import JiraAdmin, JiraProjectReader

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]


async def test_jira_admin_create_and_update_project(harness):
    a, r = harness.impl(JiraAdmin), harness.impl(JiraProjectReader)
    p = await a.create_project("NEW", "New project", template="scrum", lead="ann@example.com")
    assert p.key == "NEW" and p.name == "New project"
    assert "NEW" in [x.key for x in (await r.list_projects()).items]
    updated = await a.update_project("NEW", name="Renamed")
    assert updated.name == "Renamed"


async def test_jira_admin_workflows_screens_and_editor_link(harness):
    a = harness.impl(JiraAdmin)
    wfs = await a.list_workflows()
    assert wfs[0].name == "Software workflow" and "To Do" in wfs[0].statuses
    assert (await a.get_workflow_editor_link("wf1")).endswith("/jira/settings/issues/workflows/wf1")
    assert (await a.get_screen("1")).name == "Default screen"
    assert (await a.update_screen("1", name="Renamed")).name == "Renamed"


async def test_jira_admin_project_template_and_lead_are_sent_resolved(harness):
    if harness.server is None:
        pytest.skip("wire-level assertion")
    await harness.impl(JiraAdmin).create_project("NEW", "N", template="tpl", lead="ann@example.com")
    sent = next(r for r in harness.server.requests if (r["method"], r["path"]) == ("POST", "/rest/api/3/project"))
    assert sent["json"]["projectTemplateKey"] == "tpl" and sent["json"]["leadAccountId"] == "acc-ann"
