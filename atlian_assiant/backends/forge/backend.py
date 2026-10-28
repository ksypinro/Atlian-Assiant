"""FORGE backend: Forge App REST client for the companion app (design 10.10). Implements no catalog operations; serves the event plane."""
from __future__ import annotations

from typing import ClassVar

from ...capabilities import BackendKind, Deployment
from ...config import AtlassianConfig
from ...core.http import HttpTransport
from ...core.services import CoreServices
from ...factory import Availability
from ...protocols.base import BackendHealth
from .app_rest import ForgeAppRestClient


class ForgeBackend:
    kind: ClassVar[BackendKind] = BackendKind.FORGE
    deployments: ClassVar[frozenset[Deployment]] = frozenset({Deployment.CLOUD})

    def __init__(self, app_rest: ForgeAppRestClient, core: CoreServices | None = None) -> None:
        self.app_rest, self._core = app_rest, core

    def implementation_for(self, protocol: type) -> object | None:
        return None

    def supported_operations(self) -> frozenset[str]:
        return frozenset()          # no catalog operations: the Forge backend feeds EventSources (ForgeInboxSource / ForgeSubscriptionManager)

    async def health(self) -> BackendHealth:
        try:
            await self.app_rest.health()
        except Exception as exc:
            return BackendHealth("unavailable", f"{type(exc).__name__}: {exc}")
        return BackendHealth("degraded", "Forge App REST API is Preview (EV section 15)")

    async def aclose(self) -> None:
        if self._core is not None:
            await self._core.aclose()


class ForgeBackendFactory:
    kind: ClassVar[BackendKind] = BackendKind.FORGE
    deployments: ClassVar[frozenset[Deployment]] = frozenset({Deployment.CLOUD})
    extra: ClassVar[str | None] = None

    def availability(self, config: AtlassianConfig) -> Availability:
        if not config.preview_features_allowed:
            return Availability(False, "Forge App REST API is Preview: set preview_features_allowed=true to use it")
        if not (config.forge_app_id and config.forge_env_id):
            return Availability(False, "ATLASSIAN_FORGE_APP_ID and ATLASSIAN_FORGE_ENV_ID are not set")
        if not (config.oauth_client_id and config.oauth_refresh_token):
            return Availability(False, "Forge App REST needs OAuth 2.0 (3LO) credentials with the app's scopes (ATLASSIAN_OAUTH_*)")
        return Availability(True)

    def create(self, config: AtlassianConfig, core: CoreServices) -> ForgeBackend:
        transport: HttpTransport = core.cloud_transport("forge")
        assert config.forge_app_id and config.forge_env_id
        client = ForgeAppRestClient(transport, app_id=config.forge_app_id, env_id=config.forge_env_id, cloud_id=config.cloud_id or "",
                                    base_override=str(config.forge_app_rest_base) if config.forge_app_rest_base else None)
        return ForgeBackend(client, core)
