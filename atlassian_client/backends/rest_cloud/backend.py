"""RestBackend: the primary deterministic backend for Atlassian Cloud (design section 10.1)."""
from __future__ import annotations

from typing import ClassVar

from ...capabilities import BackendKind, Deployment
from ...config import AtlassianConfig
from ...core.http import HttpTransport
from ...core.services import CoreServices
from ...factory import Availability
from ...protocols.base import BackendHealth
from .agile import AgileRest
from .confluence import ConfluenceRest
from .identity import IdentityRest, RawRest
from .jira import JiraRest
from .jsm import JsmRest

# Operations implemented by this backend in P0 (read + search). Writers are added in P2.
SUPPORTED: frozenset[str] = frozenset({
    "jira.issue.get", "jira.issue.search", "jira.issue.count", "jira.issue.comments.list", "jira.issue.changelog.list",
    "jira.issue.worklogs.list", "jira.issue.transitions.list", "jira.issue.links.list", "jira.issue.remote_links.list",
    "jira.issue.attachments.list", "jira.issue.attachment.download", "jira.issue.watchers.list", "jira.issue.property.get",
    "jira.project.list", "jira.project.get", "jira.project.components.list", "jira.project.versions.list",
    "jira.meta.fields.list", "jira.meta.issue_types.list", "jira.meta.create_fields", "jira.meta.statuses.list",
    "jira.meta.priorities.list", "jira.meta.link_types.list", "jira.meta.server_info",
    "jira.user.myself", "jira.user.get", "jira.user.search", "jira.user.lookup_account_id", "jira.user.assignable",
    "jira.filter.list", "jira.dashboard.list",
    "jira.jql.parse", "jira.jql.sanitize", "jira.jql.autocomplete", "jira.jql.suggest", "jira.jql.match",
    "jira.expression.evaluate",
    "jira.agile.board.list", "jira.agile.board.get", "jira.agile.board.config", "jira.agile.board.issues",
    "jira.agile.sprint.list", "jira.agile.sprint.get", "jira.agile.sprint.issues", "jira.agile.backlog.list",
    "jira.agile.epic.list",
    "jsm.info", "jsm.service_desk.list", "jsm.request_type.list", "jsm.request.list", "jsm.request.get",
    "jsm.request.comments", "jsm.request.sla", "jsm.kb.search", "jsm.organization.list", "jsm.customer.list",
    "confluence.content.get", "confluence.content.list", "confluence.content.children", "confluence.content.ancestors",
    "confluence.content.descendants", "confluence.content.versions", "confluence.content.diff",
    "confluence.content.macros.resolve", "confluence.comment.list", "confluence.attachment.list",
    "confluence.attachment.download", "confluence.label.list", "confluence.property.get",
    "confluence.permission.get", "confluence.permission.restrictions.get", "confluence.task.list",
    "confluence.template.list", "confluence.template.get", "confluence.space.list", "confluence.space.get",
    "confluence.space.personal", "confluence.space.permissions.list", "confluence.search.cql",
    "identity.whoami", "identity.accessible_resources", "raw.request", "raw.paginate",
})


class RestBackend:
    """Cloud REST: Jira Platform v3, Jira Software, JSM, Confluence v2 + v1/CQL, identity and raw requests.

    Composed of one component per API family; ``implementation_for(protocol)`` hands the router the component that
    satisfies a protocol (see ``protocols.base.implementation_of``).
    """

    kind: ClassVar[BackendKind] = BackendKind.REST
    deployments: ClassVar[frozenset[Deployment]] = frozenset({Deployment.CLOUD})

    def __init__(self, transport: HttpTransport, core: CoreServices | None = None) -> None:
        self.transport, self._core = transport, core
        self.jira = JiraRest(transport)
        self.agile = AgileRest(transport)
        self.jsm = JsmRest(transport)
        self.confluence = ConfluenceRest(transport)
        self.identity = IdentityRest(transport)
        self.raw = RawRest(transport)
        self._parts: tuple[object, ...] = (self.jira, self.agile, self.jsm, self.confluence, self.identity, self.raw)

    def implementation_for(self, protocol: type) -> object | None:
        return next((p for p in self._parts if isinstance(p, protocol)), None)

    def supported_operations(self) -> frozenset[str]:
        return SUPPORTED

    async def health(self) -> BackendHealth:
        try:
            await self.jira.myself()
        except Exception as exc:                      # health must never raise
            return BackendHealth("unavailable", f"{type(exc).__name__}: {exc}")
        return BackendHealth("ready", f"auth={self.transport.auth.scheme}")

    async def aclose(self) -> None:
        if self._core is not None:
            await self._core.aclose()


class RestBackendFactory:
    kind: ClassVar[BackendKind] = BackendKind.REST
    deployments: ClassVar[frozenset[Deployment]] = frozenset({Deployment.CLOUD})
    extra: ClassVar[str | None] = None

    def availability(self, config: AtlassianConfig) -> Availability:
        has_basic = bool(config.email and config.api_token)
        has_oauth = bool(config.oauth_client_id and config.oauth_client_secret and config.oauth_refresh_token)
        has_key = bool(config.service_account_key)
        if not (has_basic or has_oauth or has_key):
            return Availability(False, "no Cloud credentials: set ATLASSIAN_EMAIL + ATLASSIAN_API_TOKEN, OAuth settings, "
                                       "or ATLASSIAN_SERVICE_ACCOUNT_KEY")
        if not config.site and not (config.cloud_id and (has_oauth or has_key)):
            return Availability(False, "ATLASSIAN_SITE (e.g. example.atlassian.net) is not set")
        return Availability(True)

    def create(self, config: AtlassianConfig, core: CoreServices) -> RestBackend:
        return RestBackend(core.cloud_transport("rest"), core)
