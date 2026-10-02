import pytest

from atlassian_client.core.errors import NotFoundError
from atlassian_client.protocols import JiraAgileReader

from .helpers import drain

pytestmark = [pytest.mark.layer_01]


@pytest.mark.anyio
async def test_agile_read_boards_sprints_backlog_epics(impl):
    r = impl(JiraAgileReader)
    board = (await r.list_boards()).items[0]
    assert (board.id, board.name, board.type, board.project_key) == (42, "PROJ board", "scrum", "PROJ")
    assert (await r.get_board(42)).name == "PROJ board"
    cfg = await r.get_board_config(42)
    assert [c["name"] for c in cfg.columns] == ["To Do", "Done"]
    sprint = (await r.list_sprints(42)).items[0]
    assert (sprint.id, sprint.state, sprint.goal) == (7, "active", "Ship login fix")
    assert (await r.get_sprint(7)).name == "Sprint 7"
    assert [i.key for i in (await r.list_sprint_issues(7)).items] == ["PROJ-1"]
    assert [i.key for i in (await r.list_backlog(42)).items] == ["PROJ-2"]
    assert (await r.list_epics(42)).items[0].key == "PROJ-900"


@pytest.mark.anyio
async def test_agile_read_board_issues_paginate_and_closed_sprints_filtered(impl):
    r = impl(JiraAgileReader)
    pages = await drain(r.list_board_issues, 42, limit=1)
    assert [len(p.items) for p in pages] == [1, 1]
    assert (await r.list_sprints(42, states=["closed"])).items == []


@pytest.mark.anyio
async def test_agile_read_unknown_board_is_not_found(impl):
    with pytest.raises(NotFoundError):
        await impl(JiraAgileReader).get_board(999)
