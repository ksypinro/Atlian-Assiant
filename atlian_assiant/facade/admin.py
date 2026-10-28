"""Organization admin facade: audit events (design sections 13.1, 10.11)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, cast

from ..capabilities import BackendKind
from ..models.admin import AuditEvent, AuditEventPage
from ..models.common import Page
from ..protocols import OrgAuditReader
from ..routing import Router
from ._namespace import Namespace

_List = list


class AuditClient(Namespace):
    """Organization audit log (Cloud, needs an organization API key and the ORG_ADMIN access class)."""

    _OPS = {"admin.audit.list": "events", "admin.audit.get": "event", "admin.audit.actions": "actions", "admin.audit.stream": "stream"}

    async def events(self, *, start: datetime | None = None, end: datetime | None = None, action: str | None = None, query: str | None = None,
                     cursor: str | None = None, limit: int = 200, backend: BackendKind | str | None = None) -> Page[AuditEvent]:
        """List organization audit events in a time window.

        When to use:
            Security and compliance questions ("who changed X"). For continuous consumption use `stream`.

        Args:
            start: Earliest event time.
            end: Latest event time.
            action: Only this audit action (see `actions`).
            query: Free-text filter.
            cursor: Opaque cursor from a previous page; pass it back unchanged.
            limit: Page size, 1..500.
            backend: Force one backend. Default: the preset order.

        Returns:
            A page of audit events with `next_cursor`.

        Raises:
            PolicyDeniedError: ORG_ADMIN access is not enabled.
            AuthenticationError: The organization API key is missing or invalid.

        Access: ORG_ADMIN

        Example:
            >>> page = await client.admin.audit.events(action="user_created", limit=50)
        """
        return cast("Page[AuditEvent]", await self._call("admin.audit.list", OrgAuditReader, "list_events", start=start, end=end, action=action,
                                                         query=query, cursor=cursor, limit=limit, backend=backend))

    async def event(self, event_id: str, *, backend: BackendKind | str | None = None) -> AuditEvent:
        """Fetch one audit event.

        When to use:
            You have an event id from `events` or `stream` and need its context.

        Args:
            event_id: The audit event id.
            backend: Force one backend. Default: the preset order.

        Returns:
            The audit event.

        Raises:
            NotFoundError: No such event (or no permission).
            PolicyDeniedError: ORG_ADMIN access is not enabled.

        Access: ORG_ADMIN

        Example:
            >>> ev = await client.admin.audit.event("abc-123")
        """
        return cast("AuditEvent", await self._call("admin.audit.get", OrgAuditReader, "get_event", event_id, backend=backend))

    async def actions(self, *, backend: BackendKind | str | None = None) -> _List[str]:
        """List the audit action names that can be filtered on.

        When to use:
            Before filtering `events` by `action`.

        Args:
            backend: Force one backend. Default: the preset order.

        Returns:
            Action names.

        Raises:
            PolicyDeniedError: ORG_ADMIN access is not enabled.

        Access: ORG_ADMIN

        Example:
            >>> names = await client.admin.audit.actions()
        """
        return cast("_List[str]", await self._call("admin.audit.actions", OrgAuditReader, "list_event_actions", backend=backend))

    async def stream(self, *, cursor: str | None = None, start: datetime | None = None, limit: int = 500, sort_order: Literal["asc", "desc"] = "asc",
                     backend: BackendKind | str | None = None) -> AuditEventPage:
        """Poll the events stream (ordered by processing time) from a cursor.

        When to use:
            Continuous audit consumption: checkpoint the returned cursor (it is returned even on the last page).

        Args:
            cursor: Cursor from the previous call; omit for the first call.
            start: Only for the first call: begin at this time.
            limit: Page size, 1..500.
            sort_order: `asc` (default) or `desc`.
            backend: Force one backend. Default: the preset order.

        Returns:
            Events plus the next cursor.

        Raises:
            PolicyDeniedError: ORG_ADMIN access is not enabled.

        Access: ORG_ADMIN

        Example:
            >>> page = await client.admin.audit.stream(limit=100)
        """
        return cast("AuditEventPage", await self._call("admin.audit.stream", OrgAuditReader, "poll_stream", cursor=cursor, start=start, limit=limit,
                                                       sort_order=sort_order, backend=backend))


class AdminClient(Namespace):
    """Organization administration. Currently: `audit`."""

    def __init__(self, router: Router) -> None:
        super().__init__(router)
        self.audit = AuditClient(router)
