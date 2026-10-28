"""JsmOpsProvisioner (DM-35): alert integration / notification rules through the JSM Operations API when available; otherwise guided. On-call lookup included."""
from __future__ import annotations

from typing import Any, ClassVar

from ...core.errors import AtlassianError
from .base import ProvisionMode
from .human import HumanProvisioner


class JsmOpsProvisioner(HumanProvisioner):
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-35"})
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.API, ProvisionMode.GUIDED})

    def __init__(self, ops: Any = None, *, schedule_id: str | None = None, probe: Any = None) -> None:
        super().__init__(probe=probe)
        self.ops, self.schedule_id = ops, schedule_id

    def supported(self) -> frozenset[ProvisionMode]:
        return frozenset({ProvisionMode.GUIDED} | ({ProvisionMode.API} if self.ops is not None else set()))

    def api_steps(self) -> list[str]:
        return ["Check that the Operations alert API is reachable with these credentials", "Look up who is on call" if self.schedule_id else "(no schedule configured)",
                "Integration and notification-rule creation stay guided until the API is verified (VER-22)"]

    def guided_steps(self) -> list[str]:
        return ["JSM > Operations > Settings > Integrations: add an alert integration for the project", "Notification rules: send alerts to the polling account",
                "Schedules: confirm the on-call schedule and escalation policy"]

    def warnings(self) -> tuple[str, ...]:
        return ("requires JSM Operations (on-call); the Operations API base path and notification-rule endpoints are unverified (VER-22)",)

    async def _apply_api(self) -> dict[str, str]:
        try:
            alerts = await self.ops.list_alerts(limit=1)
            on_call = await self.ops.on_call(self.schedule_id) if self.schedule_id else []
        except AtlassianError as exc:
            from ...core.errors import InvalidRequestError
            raise InvalidRequestError(f"the Operations API is not reachable: {type(exc).__name__}: {exc.message}", hint=exc.hint or "Check the entitlement and credentials.",
                                      operation="events.provision") from exc
        return {"alerts_reachable": "true", "visible_alerts": str(len(alerts)), "on_call": ",".join(on_call)}
