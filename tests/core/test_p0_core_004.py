import os
import stat

import pytest

from atlassian_client.capabilities import Access, Deployment
from atlassian_client.config import AtlassianConfig, EnvSecretProvider, FileSecretProvider


def test_settings_from_environment(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_SITE", "example.atlassian.net")
    monkeypatch.setenv("ATLASSIAN_EMAIL", "a@b.c")
    monkeypatch.setenv("ATLASSIAN_API_TOKEN", "tok-123456")
    monkeypatch.setenv("ATLASSIAN_PRESET", "agent")
    cfg = AtlassianConfig()
    assert cfg.site == "example.atlassian.net" and cfg.preset == "agent" and cfg.deployment is Deployment.CLOUD
    assert cfg.access == [Access.READ, Access.SEARCH]
    assert cfg.pin_cloud_id and not cfg.allow_credit_consuming


def test_secrets_never_appear_in_repr_or_dump(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_API_TOKEN", "super-secret-token")
    cfg = AtlassianConfig(email="a@b.c")
    assert "super-secret-token" not in repr(cfg) and "super-secret-token" not in str(cfg.model_dump())
    assert cfg.api_token is not None and cfg.api_token.get_secret_value() == "super-secret-token"


def test_unset_sections_are_none_so_backends_are_disabled():
    cfg = AtlassianConfig()
    assert cfg.org_api_key is None and cfg.pat is None and cfg.oauth_client_id is None


def test_from_profile_resolves_secret_references(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "from-env-token")
    f = tmp_path / "profiles.toml"
    f.write_text('[profiles.prod]\nsite = "prod.atlassian.net"\nemail = "me@x.com"\napi_token = "secret://env/MY_TOKEN"\n')
    cfg = AtlassianConfig.from_profile("prod", f)
    assert cfg.site == "prod.atlassian.net" and cfg.api_token.get_secret_value() == "from-env-token"
    with pytest.raises(KeyError):
        AtlassianConfig.from_profile("nope", f)


def test_secret_providers(tmp_path, monkeypatch):
    monkeypatch.setenv("X", "v")
    assert EnvSecretProvider().resolve("secret://env/X") == "v"
    with pytest.raises(ValueError):
        EnvSecretProvider().resolve("secret://keyring/X")
    with pytest.raises(KeyError):
        EnvSecretProvider().resolve("secret://env/NOT_SET_ANYWHERE")
    p = tmp_path / "tok"
    p.write_text("filetoken\n")
    assert FileSecretProvider().resolve(f"secret://file/{p}") == "filetoken"
