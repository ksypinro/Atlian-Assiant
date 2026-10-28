"""TeamsChannelProvisioner (DM-34): guided Jira-for-Teams install + channel notifications; Entra app registration for Graph delta polling."""
from __future__ import annotations

from typing import Any, ClassVar

from .base import ProvisionMode
from .human import HumanProvisioner

#: Reading channel messages needs a Graph permission whose exact name/consent model is VER-21. Resource-specific consent (RSC) limits it to ONE team.
GRAPH_PERMISSION_CANDIDATES = ("ChannelMessage.Read.Group (RSC, one team)", "ChannelMessage.Read.All (tenant-wide: avoid)")


def entra_app_guidance(team_id: str, channel_id: str) -> dict[str, Any]:
    return {"permission_preference": list(GRAPH_PERMISSION_CANDIDATES), "scope": {"team_id": team_id, "channel_id": channel_id},
            "note": "grant the narrowest permission that works (VER-21); use RSC so the app can read only this team"}


class TeamsChannelProvisioner(HumanProvisioner):
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-34", "DM-22"})
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.GUIDED})

    def __init__(self, *, team_id: str, channel_id: str, jira_projects: list[str] | None = None, probe: Any = None) -> None:
        super().__init__(probe=probe)
        self.team_id, self.channel_id, self.projects = team_id, channel_id, jira_projects or []

    @property
    def method_id(self) -> str:
        return "DM-34"

    def guided_steps(self) -> list[str]:
        return ["Install Jira Cloud for Microsoft Teams from the Teams app store (admin approval may be needed)",
                "Add the app to the channel and configure channel notifications" + (f" for {', '.join(self.projects)}" if self.projects else ""),
                f"Register an Entra app for Graph polling with: {', '.join(GRAPH_PERMISSION_CANDIDATES[:1])}; limit it to team {self.team_id} / channel {self.channel_id}",
                "Store the client secret as a SECRET; the poller uses delta queries (outbound only)"]

    def warnings(self) -> tuple[str, ...]:
        return ("the Graph permission for reading channel messages is unverified (VER-21): prefer resource-specific consent", "Graph change notifications are avoided: they need a public URL")

    def outputs(self) -> dict[str, str]:
        return {"team_id": self.team_id, "channel_id": self.channel_id}
