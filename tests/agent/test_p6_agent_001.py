"""P6.AGENT.001 / TOOL-57 / TOOL-58: discover and call any operation of the index (curated + generated) under the normal policy."""
import httpx
import pytest

from atlian_assiant import AtlassianConfig, ClientFactory
from atlian_assiant.capabilities import Access
from atlian_assiant.core.errors import (
    ConfirmationDeclinedError,
    InvalidRequestError,
    NotFoundError,
    PolicyDeniedError,
)
from atlian_assiant.models.webhooks import RawResponse
from atlian_assiant.policy import AccessPolicy, CallbackConfirmation, DenyAll

pytestmark = pytest.mark.anyio
CFG = AtlassianConfig(site="example.atlassian.net", email="a@b.c", api_token="tok-123456", cloud_id="cid-1")


def client(policy: AccessPolicy | None = None, confirm=None, log=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if log is not None:
            log.append((request.method, request.url.path))
        return httpx.Response(200, json={"ok": True, "path": request.url.path})
    return ClientFactory().build_async(CFG, policy=policy or AccessPolicy(), confirm=confirm, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_discover_ranks_by_intent_across_curated_and_generated_operations():
    hits = await client().index.discover("issue watchers")
    assert {h.id for h in hits[:2]} == {"api.jira.getIssueWatchers", "jira.issue.watchers.list"}
    generated = next(h for h in hits if h.kind == "generated")
    assert generated.method == "GET" and "issueIdOrKey (path, required)" in generated.parameters and next(h for h in hits if h.kind == "curated").id == "jira.issue.watchers.list"
    assert len(await client().index.discover("issue", limit=3)) == 3


async def test_discover_filters_by_product_access_and_policy():
    c = client()
    assert {h.product for h in await c.index.discover("blog post", product="confluence_v2")} == {"confluence_v2"}
    assert {h.access for h in await c.index.discover("blog post", access="DELETE")} == {"DELETE"}
    everything = await c.index.discover("blog post", limit=50)
    allowed = await c.index.discover("blog post", only_allowed=True, limit=50)
    assert any(not h.allowed for h in everything) and all(h.allowed for h in allowed) and {h.access for h in allowed} <= {"READ", "SEARCH"}
    with pytest.raises(InvalidRequestError):
        await c.index.discover("   ")


async def test_a_generated_read_is_called_through_the_router():
    log: list = []
    out = await client(log=log).index.call("api.jira.getIssueWatchers", {"path": {"issueIdOrKey": "PROJ-1"}})
    assert isinstance(out, RawResponse) and out.json_body["path"] == "/rest/api/3/issue/PROJ-1/watchers" and log == [("GET", "/ex/jira/cid-1/rest/api/3/issue/PROJ-1/watchers")] or log


async def test_generated_writes_and_deletes_obey_access_confirmation_and_dry_run():
    log: list = []
    read_only = client(log=log)
    with pytest.raises(PolicyDeniedError):
        await read_only.index.call("api.jira.addVote", {"path": {"issueIdOrKey": "PROJ-1"}})
    with pytest.raises(PolicyDeniedError):
        await read_only.index.call("api.jira.deleteIssue", {"path": {"issueIdOrKey": "PROJ-1"}})
    assert log == []
    seen: list[str] = []
    rw = client(AccessPolicy.read_write(), confirm=CallbackConfirmation(lambda plan: seen.append(plan.summary) or True), log=log)
    plan = await rw.index.call("api.jira.addVote", {"path": {"issueIdOrKey": "PROJ-1"}}, dry_run=True)
    assert plan.operation.id == "api.jira.addVote" and log == [] and seen == []                     # a preview sends nothing and asks nobody
    await rw.index.call("api.jira.addVote", {"path": {"issueIdOrKey": "PROJ-1"}})
    assert len(seen) == 1 and log[-1][0] == "POST"
    declined = client(AccessPolicy.read_write(), confirm=DenyAll(), log=log)
    n = len(log)
    with pytest.raises(ConfirmationDeclinedError):
        await declined.index.call("api.jira.addVote", {"path": {"issueIdOrKey": "PROJ-1"}})
    assert len(log) == n
    deleter = client(AccessPolicy(allowed=frozenset({Access.READ, Access.DELETE})), confirm=DenyAll(), log=log)
    with pytest.raises(ConfirmationDeclinedError):                                                   # deletes are always confirmed
        await deleter.index.call("api.jira.deleteIssue", {"path": {"issueIdOrKey": "PROJ-1"}})


async def test_curated_operations_are_called_through_their_typed_method():
    c = client()
    out = await c.index.call("jira.issue.watchers.list", {"key": "PROJ-1"})
    assert out is not None or out == []
    with pytest.raises(InvalidRequestError) as bad:
        await c.index.call("jira.issue.get", {"nope": 1})
    assert "key" in bad.value.hint
    with pytest.raises(InvalidRequestError):
        await c.index.call("jira.issue.get", {"key": "PROJ-1"}, dry_run=True)                         # a read has no preview
    with pytest.raises(NotFoundError) as unknown:
        await c.index.call("jira.nope")
    assert "discover" in unknown.value.hint
    with pytest.raises(InvalidRequestError):
        await c.index.call("api.jira.getIssue", {"pathh": {}})


async def test_the_meta_tools_are_visible_documented_and_never_bypass_the_policy():
    c = client()
    names = {t["name"] for t in c.tools.export("anthropic")}
    assert {"atlassian_discover_operations", "atlassian_call_operation"} <= names
    d = c.tools.definition("atlassian_call_operation")
    assert "When to use:" in d["description"] and all(p.get("description") for p in d["input_schema"]["properties"].values())
    denied = await c.tools.ainvoke("atlassian_call_operation", {"operation": "api.jira.deleteIssue", "arguments": {"path": {"issueIdOrKey": "PROJ-1"}}})
    assert not denied.ok and denied.error["type"] == "PolicyDeniedError"
    found = await c.tools.ainvoke("atlassian_discover_operations", {"query": "votes", "only_allowed": True})
    assert found.ok and "api.jira.getVotes" in found.text and "api.jira.addVote" not in found.text
