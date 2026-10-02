"""Jira Service Management REST writes (design sections 9.4, 10.1)."""
from __future__ import annotations

from typing import Any

from ...core.errors import NotFoundError
from ...core.http import RequestSpec
from ...core.urls import ApiSurface
from ...models.common import BodyInput
from ...models.jsm import CustomerRequest, RequestComment
from . import normalize as n
from .base import RestBase
from .bodies import body_text

S = ApiSurface.JSM


class JsmWriteRest(RestBase):
    async def _w(self, method: str, path: str, op: str, json: Any = None) -> Any:
        return await self.transport.json(RequestSpec(method, S, path, json=json, operation=op, idempotent=method in ("PUT", "DELETE")))

    async def create_request(self, service_desk_id: str, request_type_id: str, fields: dict[str, Any], *, on_behalf_of: str | None = None) -> CustomerRequest:
        body: dict[str, Any] = {"serviceDeskId": service_desk_id, "requestTypeId": request_type_id, "requestFieldValues": fields}
        if on_behalf_of:
            body["raiseOnBehalfOf"] = on_behalf_of
        return n.customer_request(await self._w("POST", "/request", "jsm.request.create", body))

    async def add_request_comment(self, key: str, body: BodyInput | str, *, public: bool) -> RequestComment:
        return n.request_comment(await self._w("POST", f"/request/{key}/comment", "jsm.request.comment.add", {"body": body_text(body), "public": public}))

    async def transition_request(self, key: str, transition: str, *, comment: BodyInput | str | None = None) -> None:
        options = (await self.transport.json(RequestSpec("GET", S, f"/request/{key}/transition", operation="jsm.request.transition"))).get("values", [])
        match = [t for t in options if transition in (str(t.get("id")), t.get("name"))]
        if not match:
            raise NotFoundError(f"transition {transition!r} is not available for {key}", hint="Available: " + ", ".join(str(t.get("name")) for t in options),
                                operation="jsm.request.transition")
        body: dict[str, Any] = {"id": str(match[0]["id"])}
        if comment:
            body["additionalComment"] = {"body": body_text(comment)}
        await self._w("POST", f"/request/{key}/transition", "jsm.request.transition", body)

    async def answer_approval(self, key: str, approval_id: str, *, approve: bool) -> None:
        await self._w("POST", f"/request/{key}/approval/{approval_id}", "jsm.request.approve", {"decision": "approve" if approve else "decline"})

    async def add_customers(self, service_desk_id: str, account_ids: list[str]) -> None:
        await self._w("POST", f"/servicedesk/{service_desk_id}/customer", "jsm.customer.add", {"accountIds": account_ids})
