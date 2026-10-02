import pytest

from atlassian_client.core.errors import InvalidRequestError
from atlassian_client.models.jira import SprintChange
from atlassian_client.protocols import JiraAgileReader, JiraAgileWriter

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]


async def test_agile_write_sprint_lifecycle(harness):
    w, r = harness.impl(JiraAgileWriter), harness.impl(JiraAgileReader)
    sp = await w.manage_sprint(SprintChange(action="create", board_id=42, name="Sprint 8", goal="Ship"))
    assert sp.name == "Sprint 8" and sp.state == "future"
    started = await w.manage_sprint(SprintChange(action="start", sprint_id=sp.id))
    assert started.state == "active"
    closed = await w.manage_sprint(SprintChange(action="close", sprint_id=sp.id))
    assert closed.state == "closed"
    await w.manage_sprint(SprintChange(action="delete", sprint_id=sp.id))
    assert sp.id not in [x.id for x in (await r.list_sprints(42, states=["future", "active", "closed"])).items]


async def test_agile_write_move_rank_and_board(harness):
    w, r = harness.impl(JiraAgileWriter), harness.impl(JiraAgileReader)
    await w.move_issues_to_sprint(7, ["PROJ-2"])
    assert "PROJ-2" in [i.key for i in (await r.list_sprint_issues(7)).items]
    await w.move_issues_to_backlog(["PROJ-2"])
    await w.rank_issues(["PROJ-2"], before="PROJ-1")
    b = await w.create_board("Team board", filter_id=10)
    assert b.name == "Team board" and b.id in [x.id for x in (await r.list_boards()).items]


async def test_agile_write_validation(harness):
    w = harness.impl(JiraAgileWriter)
    with pytest.raises(InvalidRequestError):
        await w.manage_sprint(SprintChange(action="create"))
    with pytest.raises(InvalidRequestError):
        await w.rank_issues(["PROJ-1"])
    with pytest.raises(InvalidRequestError):
        await w.rank_issues(["PROJ-1"], before="A-1", after="B-2")
    if harness.name == "rest":
        with pytest.raises(InvalidRequestError):
            await w.manage_sprint(SprintChange(action="start"))
        with pytest.raises(InvalidRequestError):
            await w.manage_sprint(SprintChange(action="teleport", sprint_id=7))
