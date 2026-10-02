"""Jira facade namespaces (design section 13.1)."""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from ..core.errors import InvalidRequestError
from ..models.jira import Issue, JqlParseResult
from ..routing import Router
from ._generated_agile import JiraAgileClient
from ._generated_jira import (
    JiraDashboardsClient,
    JiraExpressionsClient,
    JiraFiltersClient,
    JiraMetadataClient,
    JiraProjectsClient,
    JiraUsersClient,
)
from ._generated_jira import JiraIssuesClient as _IssuesBase
from ._generated_jira import JiraJqlClient as _JqlBase
from ._namespace import Namespace
from ._unavailable import Unavailable


class JiraIssuesClient(_IssuesBase):
    async def iter_search(self, jql: str, *, fields: Sequence[str] | None = None, page_size: int = 100,
                          max_items: int | None = None) -> AsyncIterator[Issue]:
        """Iterate ALL issues matching a JQL query across pages (for programs; agents should use `search`).

        When to use:
            Programs that need every match. Agents should page with `search` and `next_cursor`.

        Args:
            jql: JQL query.
            fields: Fields per issue.
            page_size: Issues per request.
            max_items: Stop after this many issues.

        Returns:
            An async iterator of Issue.

        Raises:
            InvalidRequestError: The JQL is invalid.

        Access: SEARCH

        Example:
            >>> [i.key async for i in client.jira.issues.iter_search("project = PROJ")]
        """
        cursor: str | None = None
        seen = 0
        while True:
            page = await self.search(jql, fields=fields, limit=page_size, cursor=cursor)
            for issue in page.items:
                yield issue
                seen += 1
                if max_items is not None and seen >= max_items:
                    return
            if not page.has_more:
                return
            cursor = page.next_cursor


class JiraJqlClient(_JqlBase):
    async def validate(self, jql: str) -> JqlParseResult:
        """Validate one JQL query and raise a helpful error if it is invalid.

        When to use:
            Before running JQL that came from a human or an LLM.

        Args:
            jql: The JQL query.

        Returns:
            JqlParseResult for a valid query.

        Raises:
            InvalidRequestError: The query is invalid; `details["errors"]` lists the problems.

        Access: READ

        Example:
            >>> await client.jira.jql.validate("project = PROJ")
        """
        results = await self.parse([jql])
        result = results[0]
        if not result.valid:
            raise InvalidRequestError("JQL is invalid: " + "; ".join(result.errors),
                                      hint="Fix the JQL (see details['errors']) or call jira_list_fields for valid field names.",
                                      details={"errors": result.errors})
        return result


class JiraClient(Namespace):
    """Jira Platform + Jira Software."""

    def __init__(self, router: Router) -> None:
        super().__init__(router)
        self.issues = JiraIssuesClient(router)
        self.projects = JiraProjectsClient(router)
        self.metadata = JiraMetadataClient(router)
        self.users = JiraUsersClient(router)
        self.filters = JiraFiltersClient(router)
        self.dashboards = JiraDashboardsClient(router)
        self.agile = JiraAgileClient(router)
        self.jql = JiraJqlClient(router)
        self.expressions = JiraExpressionsClient(router)
        self.admin: Any = Unavailable("jira.admin", "P2")
        self.api: Any = Unavailable("jira.api (generated REST tier)", "P6")
