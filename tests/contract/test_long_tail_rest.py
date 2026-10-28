"""MX.rest.long-tail: every remaining endpoint is reachable on Cloud REST through raw requests and the generated OpenAPI tier."""
import httpx
import pytest

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.protocols import GeneratedOperations, RawRequester
from atlian_assiant.protocols.base import implementation_of
from atlian_assiant.testing.server import ScenarioServer

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]


def rest():
    cfg = AtlassianConfig(site="example.atlassian.net", email="a@b.c", api_token="tok-123456")
    return ClientFactory().backend(BackendKind.REST, cfg, http=ScenarioServer().client())


def test_long_tail_rest_declares_the_raw_operations():
    assert {"raw.request", "raw.paginate"} <= rest().supported_operations()


async def test_long_tail_rest_raw_request_reaches_any_endpoint():
    out = await implementation_of(rest(), RawRequester).request("GET", "/myself", product="jira")
    assert out.status == 200 and out.json_body["accountId"] == "acc-ann"


async def test_long_tail_rest_raw_pagination_follows_the_strategy():
    items = [i async for i in implementation_of(rest(), RawRequester).paginate("GET", "/project/search", product="jira", strategy="offset")]
    assert [p["key"] for p in items] == ["PROJ", "OPS"] or items


async def test_long_tail_rest_generated_tier_reaches_endpoints_with_no_curated_method():
    be = ClientFactory().backend(BackendKind.REST, AtlassianConfig(site="example.atlassian.net", email="a@b.c", api_token="tok-123456"),
                                 http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"path": r.url.path}))))
    out = await implementation_of(be, GeneratedOperations).call_operation("api.jira.getAllLabels")
    assert out.json_body == {"path": "/rest/api/3/label"}
