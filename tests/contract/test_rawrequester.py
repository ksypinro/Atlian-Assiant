import pytest

from atlian_assiant.protocols import RawRequester

pytestmark = [pytest.mark.layer_01]


@pytest.mark.anyio
async def test_raw_request_and_paginate(harness):
    r = harness.impl(RawRequester)
    resp = await r.request("GET", "/myself", product="jira")
    assert resp.status == 200 and resp.json_body["accountId"] == "acc-ann"
    keys = [item["key"] async for item in r.paginate("POST", "/search/jql", product="jira", strategy="next_page_token",
                                                    json={"jql": "project = PROJ"})] if harness.name == "rest" else [i["key"] async for i in r.paginate("GET", "/x", product="jira", strategy="offset")]
    assert keys == ["PROJ-1", "PROJ-2", "PROJ-3"]
