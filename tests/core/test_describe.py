import json

import pytest

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.testing import FakeBackend
from atlian_assiant.testing.server import ScenarioServer

SECRET = "ultra-secret-token-value"


def client(**kw):
    return ClientFactory().build_async(AtlassianConfig(site="example.atlassian.net", email="a@b.c", api_token=SECRET), http=ScenarioServer().client(), **kw)


def test_manifest_shape_and_no_secrets():
    d = client().describe()
    assert d["site"] == "example.atlassian.net" and d["deployment"] == "cloud" and d["preset"] == "service"
    assert d["access"] == ["read", "search"] and d["backends"]["rest"].startswith("ready (")
    assert d["operations_known"] > 150 and isinstance(d["notes"], list)
    blob = json.dumps(d)
    assert SECRET not in blob and "a@b.c" not in blob


def test_notes_explain_readonly_and_credit_policy():
    notes = " ".join(client().describe()["notes"])
    assert "credits" in notes and "read-only" in notes


def test_describe_reports_unavailable_with_reason():
    c = ClientFactory().build_async(AtlassianConfig(site="x.atlassian.net"))
    d = c.describe()
    assert d["backends"]["rest"].startswith("unavailable: no Cloud credentials")


def test_help_returns_docstring_access_and_live_backends():
    text = client().help("jira.issue.search")
    assert "client.jira.issues.search" in text and "When to use" in text and "Access class: SEARCH" in text
    assert "Backends that can serve it now: rest" in text


def test_help_for_operation_without_curated_method_and_unknown():
    c = client()
    assert "no curated facade method yet" in c.help("webhook.confluence_dc.list")
    assert "Unknown operation" in c.help("jira.issue.gett") and "jira.issue" in c.help("jira.issue.gett")
    assert "Cloud only" in c.help("graph.context") and "credits" in c.help("graph.context")


@pytest.mark.anyio
async def test_health_checks_every_backend():
    h = await client().health()
    assert h["rest"].startswith("ready")
    c = ClientFactory().build_async(AtlassianConfig(site="x"), backends=[], extra_backends={BackendKind.REST: FakeBackend()})
    assert (await c.health())["rest"].startswith("ready")
