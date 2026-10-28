"""P6.REST.001: tools/codegen.py turns OpenAPI specs into the generated REST tier + operations_index.json (design 10.1)."""
from __future__ import annotations

import json

import httpx
import pytest

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.backends.rest_cloud.generated import classify, get, load_index
from atlian_assiant.core.errors import InvalidRequestError, NotFoundError
from atlian_assiant.protocols import GeneratedOperations
from atlian_assiant.protocols.base import implementation_of
from tools import codegen

pytestmark = pytest.mark.anyio
CFG = AtlassianConfig(site="example.atlassian.net", email="a@b.c", api_token="tok-123456")


def backend(handler):
    return ClientFactory().backend(BackendKind.REST, CFG, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_the_committed_index_is_exactly_what_the_vendored_specs_generate_and_everything_is_reachable():
    assert codegen.render(codegen.build_index()) == codegen.OUT.read_text(encoding="utf-8")
    assert codegen.check_reachability() == []
    assert codegen.main(["--check"]) == 0 and codegen.main(["--check-reachability"]) == 0


def test_every_spec_operation_is_in_the_index_with_a_unique_id_surface_and_access_class():
    index = load_index()
    total = sum(1 for f in codegen.SPECS.glob("*.json") for item in json.loads(f.read_text())["paths"].values() for m in item if m in codegen.METHODS)
    assert len(index) == total >= 40
    assert {o.api for o in index.values()} == {"jira", "agile", "jsm", "confluence_v2", "confluence_v1"}
    assert get("api.jira.getIssue").access == "read" and get("api.jira.deleteIssue").access == "delete" and get("api.jira.addVote").access == "write"
    assert get("api.jira.searchForIssuesUsingJqlEnhancedSearch").access == "search"
    assert get("api.jira.getIssue").path == "/issue/{issueIdOrKey}" and get("api.confluence_v2.getBlogPosts").path == "/blogposts"


def test_access_classification_is_conservative():
    assert classify("get", "/x") == "read" and classify("DELETE", "/x/1") == "delete" and classify("POST", "/search/jql") == "search"
    assert classify("PUT", "/x") == "write" and classify("PATCH", "/x") == "write" and classify("POST", "/issue") == "write"


async def test_a_generated_operation_is_a_normal_authenticated_request_through_the_shared_transport():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"watchCount": 2})

    gen = implementation_of(backend(handler), GeneratedOperations)
    out = await gen.call_operation("api.jira.getIssueWatchers", path_params={"issueIdOrKey": "PROJ-1"})
    assert out.status == 200 and out.json_body == {"watchCount": 2}
    assert str(seen[0].url) == "https://example.atlassian.net/rest/api/3/issue/PROJ-1/watchers" and seen[0].headers["authorization"].startswith("Basic ")
    await gen.call_operation("api.agile.getEpic", path_params={"epicIdOrKey": "E-1"})
    await gen.call_operation("api.confluence_v2.getBlogPosts", query={"limit": 5, "x": None})
    await gen.call_operation("api.jira.addVote", path_params={"issueIdOrKey": "PROJ-1"})
    assert [str(r.url) for r in seen[1:]] == ["https://example.atlassian.net/rest/agile/1.0/epic/E-1", "https://example.atlassian.net/wiki/api/v2/blogposts?limit=5",
                                              "https://example.atlassian.net/rest/api/3/issue/PROJ-1/votes"] and seen[3].method == "POST"


async def test_bad_calls_fail_before_anything_is_sent():
    sent = []
    gen = implementation_of(backend(lambda r: sent.append(r) or httpx.Response(200, json={})), GeneratedOperations)
    with pytest.raises(InvalidRequestError) as missing:
        await gen.call_operation("api.jira.getIssue")
    assert "issueIdOrKey" in str(missing.value)
    with pytest.raises(InvalidRequestError):
        await gen.call_operation("api.jira.getIssue", path_params={"issueIdOrKey": "A-1"}, body={"x": 1})
    with pytest.raises(NotFoundError) as unknown:
        await gen.call_operation("api.jira.nope")
    assert "atlassian_discover_operations" in unknown.value.hint and sent == []


def test_the_rest_backend_declares_every_generated_operation_so_the_router_can_choose_it():
    be = backend(lambda r: httpx.Response(200))
    assert set(load_index()) <= be.supported_operations()


def test_codegen_rejects_specs_it_does_not_know_and_paths_outside_the_declared_prefix(tmp_path):
    (tmp_path / "mystery.json").write_text("{}")
    with pytest.raises(SystemExit):
        codegen.build_index(tmp_path)
    (tmp_path / "mystery.json").unlink()
    (tmp_path / "jira-v3.json").write_text(json.dumps({"paths": {"/other/x": {"get": {"operationId": "x"}}}}))
    with pytest.raises(SystemExit):
        codegen.build_index(tmp_path)
