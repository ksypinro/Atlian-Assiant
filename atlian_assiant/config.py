"""Configuration for every backend. Unset sections simply disable that backend."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field, HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .capabilities import Access, BackendKind, Deployment

_MCP_AUTH_MODES = frozenset({"oauth", "api_token", "service_account"})


class AtlassianConfig(BaseSettings):
    """All settings for every backend. Unset sections simply disable that backend."""

    model_config = SettingsConfigDict(env_prefix="ATLASSIAN_", env_file=".env", extra="ignore")

    # --- tenancy -------------------------------------------------------------
    deployment: Deployment = Deployment.CLOUD
    site: str | None = None                      # "example.atlassian.net"
    cloud_id: str | None = None                  # resolved automatically when None
    pin_cloud_id: bool = True                    # refuse calls to other cloudIds (DA §7.3)

    # --- Cloud REST auth (pick one) --------------------------------------------
    email: str | None = None
    api_token: SecretStr | None = None           # Basic: email + API token
    service_account_key: SecretStr | None = None # Bearer API key (service account)
    oauth_client_id: str | None = None
    oauth_client_secret: SecretStr | None = None
    oauth_refresh_token: SecretStr | None = None
    oauth_scopes: list[str] = Field(default_factory=list)
    oauth_token_store: Path | None = None        # where rotated refresh tokens are persisted

    # --- MCP ---------------------------------------------------------------------
    mcp_url: HttpUrl = HttpUrl("https://mcp.atlassian.com/v2/mcp")
    mcp_auth: str = "oauth"                      # "oauth" | "api_token" | "service_account"
    mcp_api_token: SecretStr | None = None       # a *scoped* API token for the Rovo MCP server (else api_token is used)
    mcp_flat_tools: bool = False                 # use ?tools=all flat catalog

    # --- Data Center -------------------------------------------------------------
    jira_dc_base_url: HttpUrl | None = None
    confluence_dc_base_url: HttpUrl | None = None
    pat: SecretStr | None = None                 # DC personal access token
    dc_basic_user: str | None = None             # discouraged fallback
    dc_basic_password: SecretStr | None = None

    # --- Organization admin ------------------------------------------------------
    org_id: str | None = None
    org_api_key: SecretStr | None = None

    # --- Forge companion app ------------------------------------------------------
    forge_app_id: str | None = None
    forge_env_id: str | None = None
    forge_app_rest_base: HttpUrl | None = None   # overrides computed /svc/... URL
    preview_features_allowed: bool = False       # Forge App REST API and Dynamic Modules are Preview (EV 15, 12)

    # --- Data Lake ---------------------------------------------------------------
    delta_sharing_profile: Path | None = None

    # --- CLIs / browser / DB ------------------------------------------------------
    acli_path: str = "acli"
    twg_path: str = "twg"
    jira_cli_path: str = "jira"
    browser_profile_dir: Path | None = None      # persistent Playwright profile (human does SSO)
    browser_headless: bool = True
    db_url: SecretStr | None = None              # DC only; requires allow_direct_db
    db_expected_product_version: str | None = None

    # --- Selection & policy -------------------------------------------------------
    preset: str = "service"                      # service | agent | shell | analytics | data_center
    enabled_backends: list[BackendKind] | None = None   # None = every backend that is available
    access: list[Access] = Field(default_factory=lambda: [Access.READ, Access.SEARCH])
    allow_credit_consuming: bool = False
    allow_direct_db: bool = False
    allow_browser_writes: bool = False

    # --- Transport ------------------------------------------------------------------
    timeout_seconds: float = 30.0
    max_concurrency: int = 8
    max_retries: int = 6
    user_agent_suffix: str | None = None

    @model_validator(mode="after")
    def _check_mcp_auth(self) -> AtlassianConfig:
        if self.mcp_auth not in _MCP_AUTH_MODES:
            from .core.errors import (
                ConfigurationError,  # not a ValueError: pydantic would echo the input, which may be a pasted token
            )

            raise ConfigurationError("ATLASSIAN_MCP_AUTH must be one of: " + ", ".join(sorted(_MCP_AUTH_MODES)),
                                     hint="It names the login mode, not a credential. Put a scoped MCP token in ATLASSIAN_MCP_API_TOKEN.")
        return self

    @classmethod
    def from_profile(cls, name: str, path: Path | None = None, secrets: SecretProvider | None = None) -> AtlassianConfig:
        """Load a named profile from ``~/.config/atlian-assiant/profiles.toml``.

        Values of the form ``secret://<provider>/<key>`` are resolved through ``secrets``.
        """
        path = path or Path.home() / ".config" / "atlian-assiant" / "profiles.toml"
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        try:
            profile: dict[str, Any] = dict(data["profiles"][name])
        except KeyError:
            raise KeyError(f"profile {name!r} not found in {path}") from None
        resolver = secrets or EnvSecretProvider()
        resolved = {k: resolver.resolve(v) if isinstance(v, str) and v.startswith("secret://") else v
                    for k, v in profile.items()}
        return cls.model_validate(resolved)


class SecretProvider(Protocol):
    """Resolves ``secret://provider/key`` references (env, keyring, file, cloud secret managers)."""

    def resolve(self, reference: str) -> str: ...


class EnvSecretProvider:
    """``secret://env/NAME`` reads the environment variable NAME."""

    def resolve(self, reference: str) -> str:
        scheme, _, rest = reference.partition("://")
        provider, _, key = rest.partition("/")
        if scheme != "secret" or provider != "env" or not key:
            raise ValueError(f"EnvSecretProvider cannot resolve {reference!r}")
        try:
            return os.environ[key]
        except KeyError:
            raise KeyError(f"environment variable {key} (from {reference}) is not set") from None


class FileSecretProvider:
    """``secret://file/<absolute path>`` reads the (stripped) contents of a file."""

    def resolve(self, reference: str) -> str:
        path = reference.split("://", 1)[1].split("/", 1)[1]
        return Path("/" + path.lstrip("/")).read_text(encoding="utf-8").strip()
