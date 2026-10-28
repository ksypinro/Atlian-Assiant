"""Jira Software (Agile) REST reads (design sections 3.2, 9.3)."""
from __future__ import annotations

from collections.abc import Sequence

from ...core.http import RequestSpec
from ...core.pagination import OffsetPagination
from ...core.urls import ApiSurface
from ...models.common import Page
from ...models.jira import Board, BoardConfig, Epic, Issue, Sprint
from . import normalize as n
from .base import RestBase, csv

A = ApiSurface.JIRA_AGILE


class AgileRest(RestBase):
    async def list_boards(self, *, project: str | None = None, name: str | None = None, limit: int = 50,
                          cursor: str | None = None) -> Page[Board]:
        spec = RequestSpec("GET", A, "/board", params={"projectKeyOrId": project, "name": name}, operation="jira.agile.board.list")
        return await self._page(spec, OffsetPagination("values"), n.board, limit=limit, cursor=cursor)

    async def get_board(self, board_id: int) -> Board:
        return n.board(await self._json(A, f"/board/{board_id}", operation="jira.agile.board.get"))

    async def get_board_config(self, board_id: int) -> BoardConfig:
        return n.board_config(await self._json(A, f"/board/{board_id}/configuration", operation="jira.agile.board.config"))

    async def list_board_issues(self, board_id: int, *, jql: str | None = None, limit: int = 50,
                                cursor: str | None = None) -> Page[Issue]:
        spec = RequestSpec("GET", A, f"/board/{board_id}/issue", params={"jql": jql}, operation="jira.agile.board.issues")
        return await self._page(spec, OffsetPagination("issues"), lambda d: n.issue(d, self.site_url), limit=limit, cursor=cursor)

    async def list_sprints(self, board_id: int, *, states: Sequence[str] = ("active", "future"), limit: int = 50,
                           cursor: str | None = None) -> Page[Sprint]:
        spec = RequestSpec("GET", A, f"/board/{board_id}/sprint", params={"state": csv(states)}, operation="jira.agile.sprint.list")
        return await self._page(spec, OffsetPagination("values"), n.sprint, limit=limit, cursor=cursor)

    async def get_sprint(self, sprint_id: int) -> Sprint:
        return n.sprint(await self._json(A, f"/sprint/{sprint_id}", operation="jira.agile.sprint.get"))

    async def list_sprint_issues(self, sprint_id: int, *, jql: str | None = None, limit: int = 50,
                                 cursor: str | None = None) -> Page[Issue]:
        spec = RequestSpec("GET", A, f"/sprint/{sprint_id}/issue", params={"jql": jql}, operation="jira.agile.sprint.issues")
        return await self._page(spec, OffsetPagination("issues"), lambda d: n.issue(d, self.site_url), limit=limit, cursor=cursor)

    async def list_backlog(self, board_id: int, *, limit: int = 50, cursor: str | None = None) -> Page[Issue]:
        spec = RequestSpec("GET", A, f"/board/{board_id}/backlog", operation="jira.agile.backlog.list")
        return await self._page(spec, OffsetPagination("issues"), lambda d: n.issue(d, self.site_url), limit=limit, cursor=cursor)

    async def list_epics(self, board_id: int, *, limit: int = 50, cursor: str | None = None) -> Page[Epic]:
        spec = RequestSpec("GET", A, f"/board/{board_id}/epic", operation="jira.agile.epic.list")
        return await self._page(spec, OffsetPagination("values"), n.epic, limit=limit, cursor=cursor)
