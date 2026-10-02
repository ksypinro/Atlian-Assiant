"""AsyncAtlassianClient: the single documented entry point (design section 13)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .. import operations as ops
from ..agent.describe import build_manifest, help_for
from ..agent.tools import ToolRegistry
from ..capabilities import BackendKind
from ..config import AtlassianConfig
from ..core.services import CoreServices
from ..models.identity import Identity
from ..policy import AccessPolicy, ConfirmationHandler
from ..protocols import IdentityReader
from ..routing import Preset, Router
from ._generated_graph import GraphClient
from ._generated_jsm import JsmClient
from ._generated_raw import RawClient
from ._namespace import Namespace
from ._unavailable import Unavailable
from .confluence import ConfluenceClient
from .jira import JiraClient

if TYPE_CHECKING:
    from ..factory import BuildReport


class AsyncAtlassianClient(Namespace):
    """Everything Jira and Confluence offer, behind one typed, documented, agent-friendly client.

    Namespaces: ``.jira`` ``.jsm`` ``.confluence`` ``.raw`` (implemented), and ``.graph`` ``.analytics`` ``.exports``
    ``.automation`` ``.admin`` ``.webhooks`` ``.events`` ``.mirror`` ``.browser`` ``.tools`` (planned; they raise a clear
    error naming the phase). Start with ``client.describe()`` to see what is configured and why.
    """

    def __init__(self, router: Router, config: AtlassianConfig, core: CoreServices, build_report: BuildReport,
                 preset: Preset, policy: AccessPolicy, confirm: ConfirmationHandler | None = None) -> None:
        super().__init__(router)
        self.router, self.config, self.core = router, config, core
        self.build_report, self.preset, self.policy, self.confirm = build_report, preset, policy, confirm
        self.jira = JiraClient(router)
        self.jsm = JsmClient(router)
        self.confluence = ConfluenceClient(router)
        self.raw = RawClient(router)
        self.graph = GraphClient(router)
        self.analytics: Any = Unavailable("analytics", "P5")
        self.exports: Any = Unavailable("exports", "P5")
        self.automation: Any = Unavailable("automation", "P3")
        self.admin: Any = Unavailable("admin.audit", "P4")
        self.webhooks: Any = Unavailable("webhooks", "P3")
        self.events: Any = Unavailable("events", "P3")
        self.mirror: Any = Unavailable("mirror", "P6")
        self.browser: Any = Unavailable("browser", "P5")
        self.tools = ToolRegistry(self)

    # ---------------------------------------------------------------- construction
    @classmethod
    def from_env(cls, *, preset: Preset | str | None = None, **kwargs: Any) -> AsyncAtlassianClient:
        """Build a client from ATLASSIAN_* environment variables / a .env file."""
        from ..factory import ClientFactory

        return ClientFactory().build_async(AtlassianConfig(), preset=preset, **kwargs)

    @classmethod
    def from_profile(cls, name: str, **kwargs: Any) -> AsyncAtlassianClient:
        from ..factory import ClientFactory

        return ClientFactory().build_async(AtlassianConfig.from_profile(name), **kwargs)

    @classmethod
    def for_agent(cls, **kwargs: Any) -> AsyncAtlassianClient:
        """Read-only, MCP-first client for LLM agents."""
        from ..factory import ClientFactory

        return ClientFactory.for_agent(AtlassianConfig(), **kwargs)

    # ---------------------------------------------------------------- introspection
    def describe(self) -> dict[str, Any]:
        """What is configured, which backends are ready or unavailable (and why), access, and notes. Call this first."""
        return build_manifest(self)

    def help(self, name: str) -> str:
        """Documentation for an operation id such as ``jira.issue.search`` plus which backends can serve it now."""
        return help_for(self, name)

    async def health(self) -> dict[str, str]:
        """Live check of every built backend (makes one cheap request per backend)."""
        out: dict[str, str] = {}
        for kind, backend in self.router.backends.items():
            h = await backend.health()
            out[kind.value] = f"{h.status}: {h.detail}" if h.detail else h.status
        return out

    async def whoami(self) -> Identity:
        """The identity the credentials resolve to (account, site, cloudId, auth scheme, backend)."""
        return await self._call("identity.whoami", IdentityReader, "whoami")  # type: ignore[no-any-return]

    # ---------------------------------------------------------------- derived clients
    def _clone(self, router: Router, policy: AccessPolicy | None = None) -> AsyncAtlassianClient:
        return AsyncAtlassianClient(router, self.config, self.core, self.build_report, self.preset, policy or self.policy, self.confirm)

    def via(self, backend: BackendKind | str) -> AsyncAtlassianClient:
        """A view of this client that sends every call to ONE backend, e.g. ``client.via("mcp").jira.issues.get(...)``."""
        return self._clone(self.router.forced(backend))

    def with_policy(self, policy: AccessPolicy) -> AsyncAtlassianClient:
        """A view of this client under a different access policy."""
        from ..policy import PolicyGuard

        guard = PolicyGuard(policy, self.confirm, cloud_id=self.router.cloud_id or self.config.cloud_id)
        return self._clone(self.router.guarded(guard), policy)

    # ---------------------------------------------------------------- lifecycle
    async def aclose(self) -> None:
        for backend in self.router.backends.values():
            await backend.aclose()
        await self.core.aclose()

    async def __aenter__(self) -> AsyncAtlassianClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ---------------------------------------------------------------- misc
    @property
    def operations(self) -> dict[str, Any]:
        return dict(ops.CATALOG)
