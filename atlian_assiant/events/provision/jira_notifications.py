"""Email-based Jira/Confluence notification provisioners (DM-25..29): notification schemes, custom events, watchers, filter subscriptions."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from ...core.errors import InvalidRequestError
from .base import ProvisionMode
from .human import HumanProvisioner

#: recipient types of a notification scheme event (design 14.3 table)
RECIPIENT_TYPES = frozenset({"CurrentAssignee", "Reporter", "CurrentUser", "ProjectLead", "ComponentLead", "User", "Group", "ProjectRole", "AllWatchers",
                             "UserCustomField", "GroupCustomField"})
PARAMETERISED = frozenset({"User", "Group", "ProjectRole", "UserCustomField", "GroupCustomField"})
MAILBOX_WARNING = "notification emails land in the mailbox read by ImapSource: use a dedicated mailbox and a sender allow-list (notifications@... of your site)"


async def _json(client: Any, method: str, path: str, *, body: Any = None, params: Mapping[str, Any] | None = None) -> Any:
    resp = await client.raw.request(method, path, product="jira", json=body, params=params)       # raw writes are policy-gated: unsafe methods need WRITE + confirmation
    return resp.json_body


class NotificationSchemeProvisioner(HumanProvisioner):
    """DM-25: create/update a notification scheme (event -> recipients) and associate it with a project, through ``/rest/api/3/notificationscheme``."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-25"})
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.API, ProvisionMode.GUIDED})

    def __init__(self, client: Any, *, name: str, mapping: Mapping[str, Sequence[str | tuple[str, str]]], project_key: str | None = None, probe: Any = None) -> None:
        super().__init__(probe=probe)
        self.client, self.name, self.mapping, self.project_key = client, name, {k: list(v) for k, v in mapping.items()}, project_key
        self.scheme_id: str | None = None

    def validate(self) -> None:
        for event, recipients in self.mapping.items():
            for r in recipients:
                kind = r[0] if isinstance(r, tuple) else r
                if kind not in RECIPIENT_TYPES:
                    raise InvalidRequestError(f"unknown recipient type {kind!r} for event {event!r}", hint="Use: " + ", ".join(sorted(RECIPIENT_TYPES)), operation="events.provision")
                if kind in PARAMETERISED and not isinstance(r, tuple):
                    raise InvalidRequestError(f"recipient {kind} needs a value, e.g. ({kind!r}, 'value')", hint="Pass (type, parameter).", operation="events.provision")

    def supported(self) -> frozenset[ProvisionMode]:
        return self.modes if self.client is not None else frozenset({ProvisionMode.GUIDED})

    def api_steps(self) -> list[str]:
        self.validate()
        return [f"Create or update notification scheme {self.name!r} for {len(self.mapping)} event(s)"] + ([f"Associate it with project {self.project_key}"] if self.project_key else [])

    def guided_steps(self) -> list[str]:
        out = [f"Jira settings > Issues > Notification schemes > Add {self.name!r}"]
        out += [f"Event {e}: notify {', '.join(r[0] if isinstance(r, tuple) else r for r in rs)}" for e, rs in self.mapping.items()]
        return out + ([f"Project settings > Notifications of {self.project_key}: select the scheme"] if self.project_key else [])

    def warnings(self) -> tuple[str, ...]:
        return (MAILBOX_WARNING,)

    async def _apply_api(self) -> dict[str, str]:
        self.validate()
        events = {e["name"]: str(e["id"]) for e in await _json(self.client, "GET", "/events") or []}
        unknown = [e for e in self.mapping if e not in events]
        if unknown:
            raise InvalidRequestError(f"unknown notification events {unknown}", hint="Available: " + ", ".join(sorted(events)), operation="events.provision")
        scheme_events = [{"event": {"id": events[e]}, "notifications": [
            {"notificationType": r[0], "parameter": r[1]} if isinstance(r, tuple) else {"notificationType": r} for r in rs]} for e, rs in self.mapping.items()]
        found = await _json(self.client, "GET", "/notificationscheme", params={"query": self.name})
        existing = next((s for s in (found or {}).get("values", []) if s.get("name") == self.name), None)
        if existing:
            self.scheme_id = str(existing["id"])
            await _json(self.client, "PUT", f"/notificationscheme/{self.scheme_id}/notification", body={"notificationSchemeEvents": scheme_events})
        else:
            self.scheme_id = str((await _json(self.client, "POST", "/notificationscheme", body={"name": self.name, "notificationSchemeEvents": scheme_events}))["id"])
        if self.project_key:
            await _json(self.client, "PUT", f"/project/{self.project_key}", body={"notificationScheme": int(self.scheme_id)})
        return {"scheme_id": self.scheme_id, "reused": str(bool(existing)).lower()}

    async def _teardown_api(self) -> None:
        if self.scheme_id:
            await _json(self.client, "DELETE", f"/notificationscheme/{self.scheme_id}")


class CustomEventProvisioner(HumanProvisioner):
    """DM-26: custom workflow events. Listing events is an API call; creating one and wiring the post-function is guided (the APIs are unverified: VER-19)."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-26"})
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.GUIDED, ProvisionMode.API})

    def __init__(self, client: Any, *, event_name: str, workflow: str | None = None, transition: str | None = None, probe: Any = None) -> None:
        super().__init__(probe=probe)
        self.client, self.event_name, self.workflow, self.transition = client, event_name, workflow, transition

    def supported(self) -> frozenset[ProvisionMode]:
        return self.modes if self.client is not None else frozenset({ProvisionMode.GUIDED})

    def api_steps(self) -> list[str]:
        return [f"Look for the event {self.event_name!r} (GET /rest/api/3/events) and report its id",
                "Creating the event and wiring the post-function stay guided: no documented API (VER-19)"]

    def guided_steps(self) -> list[str]:
        return [f"Jira settings > System > Events > Add event {self.event_name!r}",
                f"Workflow {self.workflow or '<workflow>'} > transition {self.transition or '<transition>'} > Post functions > Fire a {self.event_name} event",
                "Add the event to the notification scheme (DM-25) so it sends mail", "Publish the workflow"]

    def warnings(self) -> tuple[str, ...]:
        return ("custom event creation and post-function wiring have no verified API (VER-19)", MAILBOX_WARNING)

    async def _apply_api(self) -> dict[str, str]:
        events = {e["name"]: str(e["id"]) for e in await _json(self.client, "GET", "/events") or []}
        if self.event_name not in events:
            raise InvalidRequestError(f"event {self.event_name!r} does not exist yet", hint="Create it in the admin UI first:\n" + "\n".join(self.guided_steps()), operation="events.provision")
        return {"event_id": events[self.event_name]}


class WatchProvisioner(HumanProvisioner):
    """DM-27 (Jira watchers) and DM-28 (Confluence watches), through the facade writes (policy and confirmation apply)."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-27", "DM-28"})
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.API, ProvisionMode.GUIDED})

    def __init__(self, client: Any, *, product: str, targets: Sequence[str] = (), account_id: str | None = None, kind: str = "content", probe: Any = None) -> None:
        super().__init__(probe=probe)
        if product not in ("jira", "confluence"):
            raise InvalidRequestError(f"unknown product {product!r}", hint="Use jira or confluence.")
        if kind not in ("content", "space", "label"):
            raise InvalidRequestError(f"unknown watch kind {kind!r}", hint="Use content, space or label.")
        self.client, self.product, self.targets, self.account_id, self.kind = client, product, list(targets), account_id, kind
        self.done: list[str] = []

    @property
    def method_id(self) -> str:
        return "DM-27" if self.product == "jira" else "DM-28"

    def supported(self) -> frozenset[ProvisionMode]:
        return self.modes if self.client is not None else frozenset({ProvisionMode.GUIDED})

    def api_steps(self) -> list[str]:
        return [f"Watch {len(self.targets)} {'issue(s)' if self.product == 'jira' else self.kind + '(s)'} as the mailbox user: {', '.join(self.targets)}"]

    def guided_steps(self) -> list[str]:
        where = "open each issue > Watch" if self.product == "jira" else "open each page/space > ... > Watch (immediate or digest in Settings > Email)"
        return [f"{where}: {', '.join(self.targets)}"]

    def warnings(self) -> tuple[str, ...]:
        extra = ("Confluence digest mail is split into one event per referenced page",) if self.product == "confluence" else ()
        return (MAILBOX_WARNING, *extra)

    async def _apply_api(self) -> dict[str, str]:
        for t in self.targets:
            if self.product == "jira":
                await self.client.jira.issues.watch(t, account_id=self.account_id)
            else:
                await self.client.confluence.social.watch(**{{"content": "content_id", "space": "space_key", "label": "label"}[self.kind]: t})
            self.done.append(t)
        return {"watching": ",".join(self.done)}

    async def _teardown_api(self) -> None:
        for t in self.done:
            if self.product == "jira":
                await self.client.jira.issues.watch(t, watch=False, account_id=self.account_id)
            else:
                await self.client.confluence.social.watch(**{{"content": "content_id", "space": "space_key", "label": "label"}[self.kind]: t}, watch=False)
        self.done = []


class FilterSubscriptionProvisioner(HumanProvisioner):
    """DM-29: create and share a filter through REST; read existing subscriptions (``expand=subscriptions``); subscription creation is guided/browser (VER-18)."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-29"})
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.API, ProvisionMode.GUIDED, ProvisionMode.BROWSER})

    def __init__(self, client: Any, *, name: str, jql: str, schedule: str = "daily at 08:00", allow_browser_writes: bool = False, probe: Any = None) -> None:
        super().__init__(probe=probe)
        self.client, self.name, self.jql, self.schedule, self.browser = client, name, jql, schedule, allow_browser_writes
        self.filter_id: str | None = None

    def supported(self) -> frozenset[ProvisionMode]:
        base = {ProvisionMode.GUIDED} | ({ProvisionMode.API} if self.client is not None else set())
        return frozenset(base | ({ProvisionMode.BROWSER} if self.browser else set()))

    def api_steps(self) -> list[str]:
        return [f"Create (or reuse) filter {self.name!r} with JQL {self.jql!r}", "Read its subscriptions (expand=subscriptions)",
                "Creating the subscription itself is guided: no verified REST support (VER-18)"]

    def guided_steps(self) -> list[str]:
        return [f"Filters > {self.name} > ... > Subscribe > schedule: {self.schedule}; recipients: the mailbox user", "Save the subscription"]

    def warnings(self) -> tuple[str, ...]:
        return ("filter subscription creation is guided: REST support is unverified (VER-18)", "digest mail is split into one event per issue", MAILBOX_WARNING)

    async def _apply_api(self) -> dict[str, str]:
        found = await _json(self.client, "GET", "/filter/search", params={"filterName": self.name, "expand": "subscriptions,jql"})
        existing = next((f for f in (found or {}).get("values", []) if f.get("name") == self.name), None)
        if existing:
            self.filter_id = str(existing["id"])
            subs = (existing.get("subscriptions") or {}).get("items", [])
        else:
            created = await _json(self.client, "POST", "/filter", body={"name": self.name, "jql": self.jql, "sharePermissions": [{"type": "authenticated"}]})
            self.filter_id, subs = str(created["id"]), []
        return {"filter_id": self.filter_id, "subscriptions": str(len(subs)), "next": "create the subscription in the UI" if not subs else "subscription exists"}

    async def _teardown_api(self) -> None:
        if self.filter_id:
            await _json(self.client, "DELETE", f"/filter/{self.filter_id}")
