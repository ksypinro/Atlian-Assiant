"""Webhook management and Automation facade namespaces (design sections 13.1, 14.3, 14.15)."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast

from ..capabilities import BackendKind
from ..core.errors import UnsupportedDeliveryPathError
from ..models.webhooks import FailedWebhook, WebhookRegistration, WebhookSubscription
from ..policy import PlannedAction
from ..protocols import AdminWebhookManager, AutomationTrigger, WebhookManager
from ._namespace import Namespace

_List = list


class WebhooksClient(Namespace):
    """Register, refresh and inspect Jira webhooks (dynamic and admin families). All methods are MANAGE access: always confirmed."""

    _OPS = {"webhook.jira.dynamic.list": "list", "webhook.jira.dynamic.register": "register", "webhook.jira.dynamic.delete": "delete",
            "webhook.jira.dynamic.refresh": "refresh", "webhook.jira.dynamic.failed": "failed", "webhook.jira.admin.list": "list_admin",
            "webhook.jira.admin.register": "register_admin", "webhook.jira.admin.update": "update_admin",
            "webhook.jira.admin.delete": "delete_admin"}

    async def list(self, *, backend: BackendKind | str | None = None) -> _List[WebhookRegistration]:
        """List the dynamic webhooks registered by this app/credential.

        When to use:
            Checking what is registered and when each registration expires (dynamic webhooks expire after 30 days).

        Args:
            backend: Force one backend ("rest", ...). Default: the preset order.

        Returns:
            The registrations, each with `expires_at`.

        Raises:
            NoBackendAvailableError: No configured backend can serve this operation.
            PolicyDeniedError: MANAGE access is not enabled.

        Access: MANAGE (always confirmed)

        Example:
            >>> regs = await client.webhooks.list()
        """
        return cast("_List[WebhookRegistration]", await self._call("webhook.jira.dynamic.list", WebhookManager, "list_webhooks", backend=backend))

    async def register(self, url: str, subscriptions: Sequence[WebhookSubscription], *, secret: str | None = None, dry_run: bool = False,
                       backend: BackendKind | str | None = None) -> _List[WebhookRegistration] | PlannedAction:
        """Register dynamic webhooks (DM-03) that call `url` for the given events.

        When to use:
            Setting up Jira push events with OAuth 2.0 or Connect credentials. With API-token credentials use `register_admin` (DM-02).

        Args:
            url: Public HTTPS callback (run `WebhookUrlPreflight` first; private addresses never work).
            subscriptions: Events and an optional restricted-JQL filter per subscription.
            secret: Shared secret for signature verification, when the tenant supports it.
            dry_run: True returns the PlannedAction (what WOULD happen) without changing anything.
            backend: Force one backend. Default: the preset order.

        Returns:
            The created registrations, or the PlannedAction when `dry_run=True`.

        Raises:
            PermissionDeniedError: The credential is not an OAuth 2.0 / Connect app, or MANAGE is not enabled.
            InvalidRequestError: Unknown event or unsupported JQL.
            ConfirmationDeclinedError: The user declined.

        Access: MANAGE (always confirmed; preview with dry_run=True)

        Example:
            >>> await client.webhooks.register("https://hooks.example.com/hooks/jira", [WebhookSubscription(events=["jira:issue_updated"])], dry_run=True)
        """
        return cast("_List[WebhookRegistration] | PlannedAction", await self._call("webhook.jira.dynamic.register", WebhookManager, "register", url, subscriptions, secret=secret, dry_run=dry_run, backend=backend))

    async def delete(self, webhook_ids: Sequence[str], *, dry_run: bool = False, backend: BackendKind | str | None = None) -> None | PlannedAction:
        """Delete dynamic webhooks by id.

        When to use:
            Removing registrations you no longer need (they stop delivering immediately).

        Args:
            webhook_ids: Registration ids from `list` or `register`.
            dry_run: True returns the PlannedAction without changing anything.
            backend: Force one backend. Default: the preset order.

        Returns:
            None, or the PlannedAction when `dry_run=True`.

        Raises:
            PolicyDeniedError: MANAGE access is not enabled.
            ConfirmationDeclinedError: The user declined.

        Access: MANAGE (always confirmed; preview with dry_run=True)

        Example:
            >>> await client.webhooks.delete(["1001"], dry_run=True)
        """
        return cast("None | PlannedAction", await self._call("webhook.jira.dynamic.delete", WebhookManager, "delete", webhook_ids,
                                                             dry_run=dry_run, backend=backend))

    async def refresh(self, webhook_ids: Sequence[str], *, dry_run: bool = False,
                      backend: BackendKind | str | None = None) -> datetime | None | PlannedAction:
        """Extend the expiry of dynamic webhooks (they expire after 30 days).

        When to use:
            From a scheduled job well before expiry (the provisioner refreshes every 20 days).

        Args:
            webhook_ids: Registration ids to extend.
            dry_run: True returns the PlannedAction without changing anything.
            backend: Force one backend. Default: the preset order.

        Returns:
            The new expiry, or the PlannedAction when `dry_run=True`.

        Raises:
            PolicyDeniedError: MANAGE access is not enabled.
            ConfirmationDeclinedError: The user declined.

        Access: MANAGE (always confirmed; preview with dry_run=True)

        Example:
            >>> await client.webhooks.refresh(["1001"], dry_run=True)
        """
        return cast("datetime | None | PlannedAction", await self._call("webhook.jira.dynamic.refresh", WebhookManager, "refresh", webhook_ids,
                                                                        dry_run=dry_run, backend=backend))

    async def failed(self, *, after: datetime | None = None, backend: BackendKind | str | None = None) -> _List[FailedWebhook]:
        """List webhook deliveries that failed (DM-05), so the affected entities can be re-fetched.

        When to use:
            Recovering from receiver downtime without waiting for a reconciliation sweep.

        Args:
            after: Only failures after this time.
            backend: Force one backend. Default: the preset order.

        Returns:
            Failed deliveries (id, url, body, failure time).

        Raises:
            PolicyDeniedError: MANAGE access is not enabled.

        Access: MANAGE (always confirmed)

        Example:
            >>> failed = await client.webhooks.failed()
        """
        return cast("_List[FailedWebhook]", await self._call("webhook.jira.dynamic.failed", WebhookManager, "list_failed", after=after, backend=backend))

    async def list_admin(self, *, backend: BackendKind | str | None = None) -> _List[WebhookRegistration]:
        """List admin-family webhooks (`/rest/webhooks/1.0/webhook`).

        When to use:
            Inspecting webhooks created in the admin UI (DM-01) or by the admin REST API (DM-02).

        Args:
            backend: Force one backend. Default: the preset order.

        Returns:
            The admin webhooks.

        Raises:
            PolicyDeniedError: MANAGE access is not enabled. PermissionDeniedError: Jira admin permission is required.

        Access: MANAGE (always confirmed)

        Example:
            >>> hooks = await client.webhooks.list_admin()
        """
        return cast("_List[WebhookRegistration]", await self._call("webhook.jira.admin.list", AdminWebhookManager, "list_admin_webhooks", backend=backend))

    async def register_admin(self, name: str, url: str, events: Sequence[str], *, jql: str | None = None, exclude_body: bool = False,
                             secret: str | None = None, dry_run: bool = False,
                             backend: BackendKind | str | None = None) -> WebhookRegistration | PlannedAction:
        """Create an admin webhook (DM-02); works with API-token credentials and Jira admin permission.

        When to use:
            Jira push events when OAuth/Connect credentials are not available. Admin webhooks do not expire.

        Args:
            name: Display name.
            url: Public HTTPS callback (run `WebhookUrlPreflight` first).
            events: Event names such as "jira:issue_updated".
            jql: Optional JQL filter for issue-related events.
            exclude_body: Send events without the payload (then fetch canonical state).
            secret: Shared secret for signature verification (the exact field is a verify item).
            dry_run: True returns the PlannedAction without changing anything.
            backend: Force one backend. Default: the preset order.

        Returns:
            The registration, or the PlannedAction when `dry_run=True`.

        Raises:
            PermissionDeniedError: Jira admin permission is required, or MANAGE is not enabled.
            ConfirmationDeclinedError: The user declined.

        Access: MANAGE (always confirmed; preview with dry_run=True)

        Example:
            >>> await client.webhooks.register_admin("sync", "https://hooks.example.com/hooks/jira", ["jira:issue_updated"], dry_run=True)
        """
        return cast("WebhookRegistration | PlannedAction", await self._call("webhook.jira.admin.register", AdminWebhookManager, "register_admin_webhook", name, url, events, jql=jql, exclude_body=exclude_body,
            secret=secret, dry_run=dry_run, backend=backend))

    async def update_admin(self, webhook_id: str, name: str, url: str, events: Sequence[str], *, jql: str | None = None, exclude_body: bool = False,
                           secret: str | None = None, dry_run: bool = False,
                           backend: BackendKind | str | None = None) -> WebhookRegistration | PlannedAction:
        """Replace an admin webhook's settings.

        When to use:
            Changing events, JQL or the callback URL of an existing admin webhook.

        Args:
            webhook_id: Id from `list_admin`.
            name: Display name.
            url: Public HTTPS callback.
            events: Event names.
            jql: Optional JQL filter.
            exclude_body: Send events without the payload.
            secret: Shared secret.
            dry_run: True returns the PlannedAction without changing anything.
            backend: Force one backend. Default: the preset order.

        Returns:
            The updated registration, or the PlannedAction when `dry_run=True`.

        Raises:
            NotFoundError: No such webhook. PolicyDeniedError: MANAGE access is not enabled.
            ConfirmationDeclinedError: The user declined.

        Access: MANAGE (always confirmed; preview with dry_run=True)

        Example:
            >>> await client.webhooks.update_admin("12", "sync", "https://hooks.example.com/hooks/jira", ["jira:issue_created"], dry_run=True)
        """
        return cast("WebhookRegistration | PlannedAction", await self._call("webhook.jira.admin.update", AdminWebhookManager, "update_admin_webhook", webhook_id, name, url, events, jql=jql,
            exclude_body=exclude_body, secret=secret, dry_run=dry_run, backend=backend))

    async def delete_admin(self, webhook_id: str, *, dry_run: bool = False, backend: BackendKind | str | None = None) -> None | PlannedAction:
        """Delete an admin webhook.

        When to use:
            Removing an admin webhook you no longer need.

        Args:
            webhook_id: Id from `list_admin`.
            dry_run: True returns the PlannedAction without changing anything.
            backend: Force one backend. Default: the preset order.

        Returns:
            None, or the PlannedAction when `dry_run=True`.

        Raises:
            NotFoundError: No such webhook. PolicyDeniedError: MANAGE access is not enabled.
            ConfirmationDeclinedError: The user declined.

        Access: MANAGE (always confirmed; preview with dry_run=True)

        Example:
            >>> await client.webhooks.delete_admin("12", dry_run=True)
        """
        return cast("None | PlannedAction", await self._call("webhook.jira.admin.delete", AdminWebhookManager, "delete_admin_webhook", webhook_id,
                                                             dry_run=dry_run, backend=backend))

    @property
    def confluence_cloud(self) -> Any:
        """Confluence Cloud has no REST webhook API (EV section 46): this always raises with the supported alternatives."""
        raise UnsupportedDeliveryPathError(
            "Confluence Cloud has no REST webhook registration API",
            hint="Use a Forge app (DM-06 native, DM-07 inbox, DM-09 remote), a Confluence Automation 'Send web request' rule (DM-16), "
                 "a Connect webhook module (DM-04, legacy) or CQL polling (DM-37).")


class AutomationClient(Namespace):
    """Fire Jira Automation rules that use an incoming-webhook trigger."""

    _OPS = {"automation.incoming_webhook.trigger": "trigger_incoming_webhook"}

    async def trigger_incoming_webhook(self, webhook_url: str, *, secret: str | None = None, issue_keys: Sequence[str] = (),
                                       data: Mapping[str, Any] | None = None, dry_run: bool = False,
                                       backend: BackendKind | str | None = None) -> None | PlannedAction:
        """Fire an Automation rule through its incoming-webhook URL.

        When to use:
            Starting a rule from Python (the rule can then notify, transition or call out). The URL is the capability: keep it secret.

        Args:
            webhook_url: The URL from the rule's "Incoming webhook" trigger (https://automation.atlassian.com/pg/webhooks/...).
            secret: The rule's webhook secret, sent as `X-Automation-Webhook-Token`.
            issue_keys: Issues the rule should run for.
            data: Extra JSON available to the rule as smart values.
            dry_run: True returns the PlannedAction without calling anything.
            backend: Force one backend. Default: the preset order.

        Returns:
            None, or the PlannedAction when `dry_run=True`.

        Raises:
            ConfigurationError: The URL is not an Automation incoming-webhook host (credentials are never sent elsewhere).
            PolicyDeniedError: Write access is not enabled.
            ConfirmationDeclinedError: The user declined.

        Access: WRITE (changes data; the user confirms; preview with dry_run=True)

        Example:
            >>> await client.automation.trigger_incoming_webhook("https://automation.atlassian.com/pg/webhooks/abc", issue_keys=["PROJ-1"], dry_run=True)
        """
        return cast("None | PlannedAction", await self._call("automation.incoming_webhook.trigger", AutomationTrigger, "trigger_incoming_webhook",
                                                             webhook_url, secret=secret, issue_keys=issue_keys, data=data, dry_run=dry_run, backend=backend))
