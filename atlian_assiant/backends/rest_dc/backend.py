"""RestDcBackend: Jira / Confluence Data Center REST (design section 10.2)."""
from __future__ import annotations

from typing import Any, ClassVar

from ...capabilities import BackendKind, Deployment
from ...config import AtlassianConfig
from ...core.http import HttpTransport
from ...core.services import CoreServices
from ...exports import SynthesizedConfluenceExporter, SynthesizedJiraExporter
from ...factory import Availability
from ...matrix import declared_operations
from ...protocols.base import BackendHealth
from ..rest_cloud.agile import AgileRest
from ..rest_cloud.agile_write import AgileWriteRest
from ..rest_cloud.identity import RawRest
from ..rest_cloud.jsm import JsmRest
from ..rest_cloud.jsm_write import JsmWriteRest
from .confluence import ConfluenceDcRest
from .confluence_write import ConfluenceDcWrite
from .jira import JiraDcRest, JiraDcWrite
from .webhooks import AutomationDcRest, WebhookDcRest

#: partial matrix cells: the subset Data Center serves (design section 11 marks these rows with a half circle)
PARTIAL_OPS: dict[str, tuple[str, ...]] = {
    "jira-jql-tools": ("jira.jql.autocomplete", "jira.jql.suggest"),
    "conf-tasks-templates-reactions": ("confluence.template.list", "confluence.template.get"),
    "conf-macros": ("confluence.content.macros.resolve",),
    "export-jira-csv": ("export.jira.csv",), "export-conf-page": ("export.confluence.page",), "export-conf-space": ("export.confluence.space",),    # synthesized (exports.py)
}
#: where a ``full`` row has no Data Center endpoint for one of its operations (kept honest and visible here)
ROW_OVERRIDES: dict[str, tuple[str, ...]] = {
    "jira-admin": ("jira.admin.workflows.list", "jira.admin.workflow.editor_link", "jira.project.create", "jira.project.update"),     # screens are not in the DC REST API
    "conf-content-write": ("confluence.content.create", "confluence.content.update", "confluence.content.move", "confluence.content.copy"),         # no archive endpoint
    "webhook-management": ("webhook.jira.admin.list", "webhook.jira.admin.register", "webhook.jira.admin.update", "webhook.jira.admin.delete",
                           "webhook.confluence_dc.list", "webhook.confluence_dc.register", "webhook.confluence_dc.update", "webhook.confluence_dc.delete",
                           "webhook.confluence_dc.test", "webhook.confluence_dc.stats"),
}


def supported() -> frozenset[str]:
    return declared_operations(BackendKind.REST_DC, PARTIAL_OPS, ROW_OVERRIDES)


class RestDcBackend:
    kind: ClassVar[BackendKind] = BackendKind.REST_DC
    deployments: ClassVar[frozenset[Deployment]] = frozenset({Deployment.DATA_CENTER})

    def __init__(self, transport: HttpTransport, core: CoreServices | None = None) -> None:
        self.transport, self._core = transport, core
        self.jira = JiraDcRest(transport)
        self.agile, self.jsm = AgileRest(transport), JsmRest(transport)
        self.confluence = ConfluenceDcRest(transport)
        self.raw = RawRest(transport)
        self.jira_write = JiraDcWrite(transport, self.jira)
        self.agile_write, self.jsm_write = AgileWriteRest(transport), JsmWriteRest(transport)
        self.confluence_write = ConfluenceDcWrite(transport, self.confluence)
        self.webhooks = WebhookDcRest(transport)
        self.automation = AutomationDcRest(transport)
        self.exports_jira = SynthesizedJiraExporter(self.jira)
        self.exports_confluence = SynthesizedConfluenceExporter(self.confluence, space_pages=self._space_pages)
        for part in (self.jira, self.agile, self.jsm, self.confluence, self.raw, self.jira_write, self.agile_write, self.jsm_write, self.confluence_write, self.webhooks, self.automation):
            part.kind = BackendKind.REST_DC
        self._parts: tuple[object, ...] = (self.jira, self.agile, self.jsm, self.confluence, self.raw, self.jira_write, self.agile_write, self.jsm_write,
                                           self.confluence_write, self.webhooks, self.automation)
        self._exporters: tuple[object, ...] = (self.exports_jira, self.exports_confluence)

    async def _space_pages(self, space_key: str, cursor: str | None) -> Any:
        return await self.confluence.list_content(space_id=space_key, limit=100, cursor=cursor)

    def implementation_for(self, protocol: type) -> object | None:
        return next((p for p in (*self._parts, *self._exporters) if isinstance(p, protocol)), None)

    def supported_operations(self) -> frozenset[str]:
        return supported()

    async def health(self) -> BackendHealth:
        try:
            info = await self.jira.server_info()
        except Exception as exc:
            return BackendHealth("unavailable", f"{type(exc).__name__}: {exc}")
        return BackendHealth("ready", f"Jira Data Center {info.version or ''} ({self.transport.auth.scheme})".strip())

    async def aclose(self) -> None:
        if self._core is not None:
            await self._core.aclose()


class RestDcBackendFactory:
    kind: ClassVar[BackendKind] = BackendKind.REST_DC
    deployments: ClassVar[frozenset[Deployment]] = frozenset({Deployment.DATA_CENTER})
    extra: ClassVar[str | None] = None

    def availability(self, config: AtlassianConfig) -> Availability:
        if config.deployment is not Deployment.DATA_CENTER:
            return Availability(False, "the deployment is Cloud: set ATLASSIAN_DEPLOYMENT=data_center for Data Center REST")
        if not (config.jira_dc_base_url or config.confluence_dc_base_url):
            return Availability(False, "ATLASSIAN_JIRA_DC_BASE_URL / ATLASSIAN_CONFLUENCE_DC_BASE_URL is not set")
        if not (config.pat or (config.dc_basic_user and config.dc_basic_password)):
            return Availability(False, "no Data Center credentials: set ATLASSIAN_PAT (preferred) or ATLASSIAN_DC_BASIC_USER + ATLASSIAN_DC_BASIC_PASSWORD")
        return Availability(True)

    def create(self, config: AtlassianConfig, core: CoreServices) -> RestDcBackend:
        return RestDcBackend(core.dc_transport("rest_dc"), core)
