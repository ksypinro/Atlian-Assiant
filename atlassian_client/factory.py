"""Backend factories, the registry, and ClientFactory (design sections 12.1 and 12.2)."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, TypeVar

import httpx

from .capabilities import BackendKind, Deployment
from .config import AtlassianConfig
from .core.errors import BackendUnavailableError
from .core.services import CoreServices
from .policy import AccessPolicy, ConfirmationHandler
from .protocols.base import Backend, implementation_of
from .routing import Preset, Router, SelectionPolicy

if TYPE_CHECKING:
    from .facade.client import AsyncAtlassianClient
    from .facade.sync import AtlassianClient

P = TypeVar("P")


@dataclass(frozen=True)
class Availability:
    ok: bool
    reason: str | None = None        # e.g. "pip install 'atlassian-client[mcp]'" / "ATLASSIAN_API_TOKEN not set"


class BackendFactory(Protocol):
    """Abstract factory for one backend family."""

    kind: ClassVar[BackendKind]
    deployments: ClassVar[frozenset[Deployment]]
    extra: ClassVar[str | None]      # pip extra that provides its dependencies

    def availability(self, config: AtlassianConfig) -> Availability: ...

    def create(self, config: AtlassianConfig, core: CoreServices) -> Backend: ...


class BackendRegistry:
    """Holds BackendFactory instances; third parties register through entry points."""

    ENTRY_POINT_GROUP = "atlassian_client.backends"

    def __init__(self) -> None:
        self._factories: dict[BackendKind, BackendFactory] = {}

    def register(self, factory: BackendFactory, *, replace: bool = False) -> None:
        if factory.kind in self._factories and not replace:
            raise ValueError(f"backend {factory.kind} already registered")
        self._factories[factory.kind] = factory

    def load_entry_points(self) -> None:
        for ep in entry_points(group=self.ENTRY_POINT_GROUP):
            self.register(ep.load()(), replace=True)

    def factories(self) -> Mapping[BackendKind, BackendFactory]:
        return dict(self._factories)

    def get(self, kind: BackendKind) -> BackendFactory:
        try:
            return self._factories[kind]
        except KeyError:
            raise BackendUnavailableError(f"backend {kind.value} is not registered",
                                          hint="Install the package/extra that provides it.") from None


def default_registry() -> BackendRegistry:
    from .backends import builtin_factories

    registry = BackendRegistry()
    for factory in builtin_factories():
        registry.register(factory)
    registry.load_entry_points()
    return registry


@dataclass
class BuildReport:
    """Why each backend was built or skipped (surfaced by ``client.describe()``)."""

    built: list[BackendKind] = field(default_factory=list)
    skipped: dict[BackendKind, str] = field(default_factory=dict)


class ClientFactory:
    """Turns configuration into a fully wired client.

    Builds only the backends that are enabled AND available, records why the others were skipped, then wires the
    Router, policy and facade.
    """

    def __init__(self, registry: BackendRegistry | None = None) -> None:
        self._registry = registry or default_registry()

    def _config(self, config: AtlassianConfig | None) -> AtlassianConfig:
        return config or AtlassianConfig()

    def build_backends(self, config: AtlassianConfig, core: CoreServices,
                       only: Sequence[BackendKind] | None = None) -> tuple[dict[BackendKind, Backend], BuildReport]:
        report = BuildReport()
        built: dict[BackendKind, Backend] = {}
        wanted = only if only is not None else config.enabled_backends
        for kind, factory in self._registry.factories().items():
            if wanted is not None and kind not in wanted:
                report.skipped[kind] = "not enabled (enabled_backends)"
                continue
            if config.deployment not in factory.deployments:
                report.skipped[kind] = f"not available on {config.deployment.value}"
                continue
            avail = factory.availability(config)
            if not avail.ok:
                report.skipped[kind] = avail.reason or "unavailable"
                continue
            built[kind] = factory.create(config, core)
            report.built.append(kind)
        return built, report

    def build_async(
        self,
        config: AtlassianConfig | None = None,
        *,
        preset: Preset | str | None = None,
        backends: Sequence[BackendKind] | None = None,
        selection: SelectionPolicy | None = None,
        policy: AccessPolicy | None = None,
        confirm: ConfirmationHandler | None = None,
        http: httpx.AsyncClient | None = None,
        extra_backends: Mapping[BackendKind, Backend] | None = None,
    ) -> AsyncAtlassianClient:
        from .facade.client import AsyncAtlassianClient

        cfg = self._config(config)
        core = CoreServices.create(cfg, http)
        built, report = self.build_backends(cfg, core, backends)
        built.update(extra_backends or {})
        chosen = Preset(preset or cfg.preset)
        if cfg.deployment is Deployment.DATA_CENTER and preset is None:
            chosen = Preset.DATA_CENTER
        sel = selection or SelectionPolicy.from_preset(chosen)
        router = Router(built, sel, site=cfg.site, cloud_id=cfg.cloud_id, deployment_cloud=cfg.deployment is Deployment.CLOUD)
        return AsyncAtlassianClient(router, cfg, core, report, chosen, policy or AccessPolicy.from_config(cfg), confirm)

    def build(self, config: AtlassianConfig | None = None, **kwargs: Any) -> AtlassianClient:
        from .facade.sync import AtlassianClient

        return AtlassianClient(self.build_async(config, **kwargs))

    def backend(self, kind: BackendKind, config: AtlassianConfig | None = None, http: httpx.AsyncClient | None = None) -> Backend:
        """Build ONE backend directly (no routing, no policy) - for tests and specialists."""
        cfg = self._config(config)
        factory = self._registry.get(kind)
        avail = factory.availability(cfg)
        if not avail.ok:
            raise BackendUnavailableError(f"backend {kind.value} unavailable: {avail.reason}", hint=avail.reason)
        return factory.create(cfg, CoreServices.create(cfg, http))

    def sub_client(self, protocol: type[P], *, backend: BackendKind, config: AtlassianConfig | None = None,
                   http: httpx.AsyncClient | None = None) -> P:
        """Return one backend typed as a protocol, e.g. ``sub_client(JiraIssueReader, backend=BackendKind.REST)``."""
        impl = implementation_of(self.backend(backend, config, http), protocol)
        if impl is None:
            raise TypeError(f"{backend} does not implement {protocol.__name__}")
        return impl  # type: ignore[no-any-return]

    # --- presets (DA 25, 28-29, 32) -------------------------------------------------------------------------------
    @classmethod
    def for_service(cls, config: AtlassianConfig | None = None) -> AsyncAtlassianClient:
        return cls().build_async(config, preset=Preset.SERVICE)

    @classmethod
    def for_agent(cls, config: AtlassianConfig | None = None, *,
                  confirm: ConfirmationHandler | None = None) -> AsyncAtlassianClient:
        return cls().build_async(config, preset=Preset.AGENT, policy=AccessPolicy.read_only(), confirm=confirm)

    @classmethod
    def for_shell(cls, config: AtlassianConfig | None = None) -> AsyncAtlassianClient:
        return cls().build_async(config, preset=Preset.SHELL)

    @classmethod
    def for_analytics(cls, config: AtlassianConfig | None = None) -> AsyncAtlassianClient:
        return cls().build_async(config, preset=Preset.ANALYTICS, policy=AccessPolicy.analytics())

    @classmethod
    def for_data_center(cls, config: AtlassianConfig | None = None) -> AsyncAtlassianClient:
        return cls().build_async(config, preset=Preset.DATA_CENTER)
