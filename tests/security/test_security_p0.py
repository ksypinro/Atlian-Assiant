"""Executable evidence for the P0 security controls of design section 17 (reviewed by a human at the gate)."""
import json
import logging
import stat
import sys
import tomllib
from pathlib import Path

import httpx
import pytest

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.core.auth import BasicTokenAuth, OAuth3LOAuth, ServiceAccountKeyAuth
from atlian_assiant.core.errors import ConfigurationError
from atlian_assiant.core.http import HttpTransport, RequestSpec
from atlian_assiant.core.urls import ApiSurface, UrlResolver
from atlian_assiant.testing.server import ScenarioServer

TOKEN = "SuperSecretApiToken123"
ROOT = Path(__file__).parents[2]


def test_sec_01_credentials_are_secretstr_rotated_tokens_are_0600_and_never_rendered(tmp_path):
    cfg = AtlassianConfig(site="x.atlassian.net", email="me@x.com", api_token=TOKEN, service_account_key="svc-key-123456")
    assert TOKEN not in repr(cfg) and "svc-key-123456" not in repr(cfg) and TOKEN not in json.dumps(cfg.model_dump(mode="json"), default=str)
    for auth in (BasicTokenAuth("me@x.com", TOKEN), ServiceAccountKeyAuth(TOKEN)):
        assert TOKEN not in repr(auth) and TOKEN not in str(auth)
    c = ClientFactory().build_async(cfg, http=ScenarioServer().client())
    assert TOKEN not in json.dumps(c.describe()) and "me@x.com" not in json.dumps(c.describe())
    store = tmp_path / "oauth.json"
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"access_token": "a", "expires_in": 3600, "refresh_token": "NEW"})))
    import asyncio
    asyncio.run(OAuth3LOAuth("i", "s", "old", store=store, http=http).refresh())
    assert stat.S_IMODE(store.stat().st_mode) == 0o600


@pytest.mark.anyio
async def test_sec_03_requests_for_another_tenant_are_blocked_when_the_cloud_id_is_pinned():
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    t = HttpTransport(http, ServiceAccountKeyAuth("k"), UrlResolver("x", "someone-elses-cloud", True), pinned_cloud_id="mine")
    with pytest.raises(ConfigurationError):
        await t.send(RequestSpec("GET", ApiSurface.JIRA_PLATFORM, "/myself"))
    with pytest.raises(ConfigurationError):                        # absolute URLs cannot be used to reach foreign hosts
        await t.send(RequestSpec("GET", ApiSurface.JIRA_PLATFORM, "https://attacker.example.com/x"))
    assert AtlassianConfig().pin_cloud_id is True                    # pinning is on by default


@pytest.mark.anyio
async def test_sec_04_logs_errors_and_hooks_never_contain_credentials(caplog):
    srv = ScenarioServer()
    be = ClientFactory().backend(BackendKind.REST, AtlassianConfig(site="x.atlassian.net", email="me@x.com", api_token=TOKEN), http=srv.client())
    captured = []
    be.transport.telemetry.on_request.append(captured.append)
    with caplog.at_level(logging.DEBUG, logger="atlian_assiant"):
        await be.jira.get_issue("PROJ-1")
        with pytest.raises(Exception) as exc:
            await be.jira.get_issue("PROJ-404")
    blob = json.dumps(captured, default=str) + caplog.text + repr(exc.value) + str(exc.value.details)
    assert TOKEN not in blob and "Basic " not in blob.replace("Basic ***", "")
    assert captured and captured[0]["headers"]["authorization"] == "***"


def test_sec_20_core_dependencies_are_minimal_heavy_ones_are_optional_extras_and_not_imported():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert {d.split(">=")[0] for d in meta["dependencies"]} == {"httpx", "pydantic", "pydantic-settings", "anyio"}
    heavy = {"playwright", "boto3", "delta-sharing", "duckdb", "azure-servicebus", "google-cloud-pubsub", "sqlalchemy", "redis", "anthropic",
             "slack-sdk", "twilio", "aiokafka", "mcp"}
    extras = {d.split(">=")[0].split("[")[0] for deps in meta["optional-dependencies"].values() for d in deps}
    assert heavy <= extras
    import subprocess

    code = "import sys, atlian_assiant; print(sorted(m for m in sys.modules if m.split('.')[0] in %r))" % (
        {"playwright", "boto3", "delta_sharing", "duckdb", "sqlalchemy", "redis", "anthropic", "slack_sdk", "twilio", "mcp"},)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    assert out == "[]"
