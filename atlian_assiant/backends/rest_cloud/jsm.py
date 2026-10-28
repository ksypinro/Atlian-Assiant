"""Jira Service Management REST reads (design sections 3.3, 9.4)."""
from __future__ import annotations

from ...core.http import RequestSpec
from ...core.pagination import JsmPagination
from ...core.urls import ApiSurface
from ...models.common import Page, UserRef
from ...models.jsm import (
    CustomerRequest,
    JsmInfo,
    KbArticle,
    Organization,
    RequestComment,
    RequestType,
    ServiceDesk,
    SlaInfo,
)
from . import normalize as n
from .base import RestBase

S = ApiSurface.JSM
EXPERIMENTAL = {"X-ExperimentalApi": "opt-in"}


class JsmRest(RestBase):
    async def info(self) -> JsmInfo:
        return n.jsm_info(await self._json(S, "/info", operation="jsm.info"))

    async def list_service_desks(self, *, limit: int = 50, cursor: str | None = None) -> Page[ServiceDesk]:
        return await self._page(RequestSpec("GET", S, "/servicedesk", operation="jsm.service_desk.list"), JsmPagination(),
                                n.service_desk, limit=limit, cursor=cursor)

    async def list_request_types(self, service_desk_id: str, *, limit: int = 50, cursor: str | None = None) -> Page[RequestType]:
        spec = RequestSpec("GET", S, f"/servicedesk/{service_desk_id}/requesttype", operation="jsm.request_type.list")
        return await self._page(spec, JsmPagination(), n.request_type, limit=limit, cursor=cursor)

    async def list_requests(self, *, service_desk_id: str | None = None, status: str | None = None, search: str | None = None,
                            limit: int = 50, cursor: str | None = None) -> Page[CustomerRequest]:
        spec = RequestSpec("GET", S, "/request", params={"serviceDeskId": service_desk_id, "requestStatus": status,
                                                         "searchTerm": search, "expand": "requestType"}, operation="jsm.request.list")
        return await self._page(spec, JsmPagination(), n.customer_request, limit=limit, cursor=cursor)

    async def get_request(self, key: str) -> CustomerRequest:
        return n.customer_request(await self._json(S, f"/request/{key}", params={"expand": "requestType"}, operation="jsm.request.get"))

    async def list_request_comments(self, key: str, *, public_only: bool = False, limit: int = 50,
                                    cursor: str | None = None) -> Page[RequestComment]:
        spec = RequestSpec("GET", S, f"/request/{key}/comment", params={"public": "true" if public_only else None},
                           operation="jsm.request.comments")
        return await self._page(spec, JsmPagination(), n.request_comment, limit=limit, cursor=cursor)

    async def get_request_sla(self, key: str) -> list[SlaInfo]:
        data = await self._json(S, f"/request/{key}/sla", operation="jsm.request.sla")
        return [n.sla(s) for s in data.get("values", [])]

    async def search_knowledge_base(self, query: str, *, service_desk_id: str | None = None, limit: int = 20) -> list[KbArticle]:
        path = f"/servicedesk/{service_desk_id}/knowledgebase/article" if service_desk_id else "/knowledgebase/article"
        data = await self._json(S, path, params={"query": query, "highlight": "true", "limit": limit}, headers=EXPERIMENTAL,
                                operation="jsm.kb.search")
        return [n.kb_article(a) for a in data.get("values", [])]

    async def list_organizations(self, *, limit: int = 50, cursor: str | None = None) -> Page[Organization]:
        spec = RequestSpec("GET", S, "/organization", headers=EXPERIMENTAL, operation="jsm.organization.list")
        return await self._page(spec, JsmPagination(), n.organization, limit=limit, cursor=cursor)

    async def list_customers(self, service_desk_id: str, *, limit: int = 50, cursor: str | None = None) -> Page[UserRef]:
        spec = RequestSpec("GET", S, f"/servicedesk/{service_desk_id}/customer", headers=EXPERIMENTAL, operation="jsm.customer.list")
        return await self._page(spec, JsmPagination(), lambda d: n.user(d) or UserRef(), limit=limit, cursor=cursor)
