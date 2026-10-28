"""JSM Operations alerts (DM-35): ``JsmOpsClient`` (list / create / acknowledge / close alerts, on-call) and ``JsmOpsAlertSource`` (polls alerts).
The API base path, field names and notification-rule endpoints are UNVERIFIED (VER-22) and isolated in this module."""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, ClassVar

import anyio
import httpx

from ...capabilities import Product
from ...core.errors import error_for_status
from ..model import ChangeAction, ChangeEvent, DeliveryInfo, EntityType, EventSourceKind
from ..stores.base import CheckpointStore


class JsmOpsClient:
    def __init__(self, http: httpx.AsyncClient, cloud_id: str, auth: tuple[str, str] | dict[str, str]) -> None:
        self.http, self.base = http, f"https://api.atlassian.com/jsm/ops/api/{cloud_id}/v1"
        self._headers = dict(auth) if isinstance(auth, dict) else {}
        self._basic = auth if isinstance(auth, tuple) else None

    async def _call(self, method: str, path: str, **kw: Any) -> Any:
        resp = await self.http.request(method, self.base + path, headers=self._headers, auth=self._basic, **kw) if self._basic else \
            await self.http.request(method, self.base + path, headers=self._headers, **kw)
        if resp.status_code >= 400:
            raise error_for_status(resp.status_code, f"{method} {path} returned {resp.status_code}", operation="jsm.ops", backend="rest")
        return resp.json() if resp.content else None

    async def list_alerts(self, *, updated_after: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit, "sort": "updatedAt", "order": "asc"}
        if updated_after:
            params["query"] = f"updatedAt > {updated_after}"
        return list((await self._call("GET", "/alerts", params=params) or {}).get("values", []))

    async def create_alert(self, message: str, *, alias: str | None = None, priority: str = "P3") -> dict[str, Any]:
        return dict(await self._call("POST", "/alerts", json={"message": message, **({"alias": alias} if alias else {}), "priority": priority}))

    async def acknowledge(self, alert_id: str) -> None:
        await self._call("POST", f"/alerts/{alert_id}/acknowledge", json={})

    async def close(self, alert_id: str) -> None:
        await self._call("POST", f"/alerts/{alert_id}/close", json={})

    async def on_call(self, schedule_id: str) -> list[str]:
        body = await self._call("GET", f"/schedules/{schedule_id}/on-calls") or {}
        return [str(p) for p in (body.get("data") or {}).get("onCallParticipants", [])]


class JsmOpsAlertSource:
    """Alerts -> ``EntityType.ALERT`` events: created / updated / closed. Dedup key ``id:updatedAt``; ordered by ``updatedAt`` with a checkpoint."""

    kind: ClassVar[EventSourceKind] = EventSourceKind.JSM_OPS_ALERTS
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-35"})
    requires_public_ingress: ClassVar[bool] = False

    def __init__(self, list_alerts: Callable[..., Awaitable[list[dict[str, Any]]]], checkpoints: CheckpointStore, *, interval: float = 60.0,
                 sleep: Callable[[float], Awaitable[None]] = anyio.sleep) -> None:
        self._list, self.checkpoints, self.interval, self._sleep = list_alerts, checkpoints, interval, sleep
        self.stream, self._closed = "jsm-ops-alerts", False

    @classmethod
    def from_client(cls, client: JsmOpsClient, checkpoints: CheckpointStore, **kw: Any) -> JsmOpsAlertSource:
        return cls(lambda **a: client.list_alerts(**a), checkpoints, **kw)

    @staticmethod
    def _action(alert: dict[str, Any]) -> ChangeAction:
        if str(alert.get("status", "")).lower() == "closed":
            return ChangeAction.CLOSED
        return ChangeAction.CREATED if alert.get("createdAt") == alert.get("updatedAt") else ChangeAction.UPDATED

    async def poll_once(self) -> list[ChangeEvent]:
        since = await self.checkpoints.load(self.stream)
        alerts = await self._list(updated_after=since)
        out, newest = [], since
        for a in alerts:
            version = str(a.get("updatedAt") or "")
            out.append(ChangeEvent(event_id=f"{a['id']}:{version}", source=self.kind, product=Product.JSM, entity_type=EntityType.ALERT, action=self._action(a),
                                   native_type=f"jsm:alert:{str(a.get('status', 'open')).lower()}", entity_id=str(a["id"]), entity_key=a.get("alias") or a.get("tinyId"),
                                   entity_version=version or None, received_at=datetime.now(UTC), delivery=DeliveryInfo(method_id="DM-35", verified=True),
                                   payload={"message": a.get("message"), "priority": a.get("priority"), "status": a.get("status")}))
            if version and (newest is None or version > newest):
                newest = version
        if newest and newest != since:
            await self.checkpoints.save(self.stream, newest)
        return out

    async def events(self) -> AsyncIterator[ChangeEvent]:
        while not self._closed:
            for ev in await self.poll_once():
                yield ev
            if self._closed:
                break
            await self._sleep(self.interval)

    async def ack(self, event: ChangeEvent) -> None:
        return None

    async def nack(self, event: ChangeEvent, *, error: BaseException) -> None:
        return None

    async def aclose(self) -> None:
        self._closed = True
