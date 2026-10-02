"""Jira Software (Agile) REST writes (design sections 9.3, 10.1)."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ...core.errors import InvalidRequestError
from ...core.http import RequestSpec
from ...core.urls import ApiSurface
from ...models.jira import Board, Sprint, SprintChange
from . import normalize as n
from .base import RestBase

A = ApiSurface.JIRA_AGILE


class AgileWriteRest(RestBase):
    async def _w(self, method: str, path: str, op: str, json: Any = None) -> Any:
        return await self.transport.json(RequestSpec(method, A, path, json=json, operation=op, idempotent=method in ("PUT", "DELETE")))

    async def manage_sprint(self, change: SprintChange) -> Sprint:
        body = {k: v for k, v in {"name": change.name, "goal": change.goal, "startDate": change.start_date, "endDate": change.end_date}.items() if v}
        a = change.action
        if a == "create":
            if change.board_id is None or not change.name:
                raise InvalidRequestError("creating a sprint needs board_id and name", hint="Set SprintChange.board_id and .name.")
            return n.sprint(await self._w("POST", "/sprint", "jira.agile.sprint.manage", {**body, "originBoardId": change.board_id}))
        if change.sprint_id is None:
            raise InvalidRequestError(f"{a} needs sprint_id", hint="Get ids from jira.agile.sprints.")
        path = f"/sprint/{change.sprint_id}"
        if a == "delete":
            await self._w("DELETE", path, "jira.agile.sprint.manage")
            return Sprint(id=change.sprint_id, name=change.name or "", state="deleted")
        state = {"start": "active", "close": "closed"}.get(a)
        if a not in ("update", "start", "close"):
            raise InvalidRequestError(f"unsupported sprint action {a!r}", hint="Use create, update, start, close or delete.")
        return n.sprint(await self._w("POST", path, "jira.agile.sprint.manage", {**body, **({"state": state} if state else {})}))

    async def move_issues_to_sprint(self, sprint_id: int, issue_keys: Sequence[str]) -> None:
        await self._w("POST", f"/sprint/{sprint_id}/issue", "jira.agile.issue.move_to_sprint", {"issues": list(issue_keys)})

    async def move_issues_to_backlog(self, issue_keys: Sequence[str]) -> None:
        await self._w("POST", "/backlog/issue", "jira.agile.issue.move_to_sprint", {"issues": list(issue_keys)})

    async def rank_issues(self, issue_keys: Sequence[str], *, before: str | None = None, after: str | None = None) -> None:
        if bool(before) == bool(after):
            raise InvalidRequestError("rank needs exactly one of before / after", hint="Pass the key to rank before or after.")
        await self._w("PUT", "/issue/rank", "jira.agile.issue.rank",
                      {"issues": list(issue_keys), **({"rankBeforeIssue": before} if before else {"rankAfterIssue": after})})

    async def create_board(self, name: str, *, filter_id: int, board_type: str = "scrum") -> Board:
        return n.board(await self._w("POST", "/board", "jira.agile.board.create", {"name": name, "type": board_type, "filterId": filter_id}))
