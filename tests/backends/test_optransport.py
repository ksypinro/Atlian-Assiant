"""OpTransport / OpBackend: REST components over a non-HTTP backend (the basis of the CLI, GraphQL, browser and lake backends)."""
import pytest

from atlian_assiant import BackendKind
from atlian_assiant.backends.optransport import OpBackend, OpTransport
from atlian_assiant.backends.rest_cloud.jira import JiraRest
from atlian_assiant.core.errors import UnsupportedOperationError
from atlian_assiant.protocols import JiraIssueReader

pytestmark = pytest.mark.anyio


class Backend(OpBackend):
    kind = BackendKind.ACLI
    COMPOSED = {"jira.user.lookup_account_id": ("jira.user.search",)}
    DIRECT = frozenset({"export.jira.csv"})


async def test_an_operation_without_a_handler_is_refused_before_anything_runs():
    ran: list[str] = []

    async def get(spec):
        ran.append(spec.path)
        return {"id": "1", "key": "A-1", "fields": {"summary": "s", "status": {"name": "Done", "statusCategory": {"name": "Done"}}}}

    t = OpTransport("acli", {"jira.issue.get": get})
    jira = JiraRest(t)
    assert (await jira.get_issue("A-1")).key == "A-1" and ran == ["/issue/A-1"]
    with pytest.raises(UnsupportedOperationError) as exc:
        await jira.list_comments("A-1")
    assert exc.value.operation == "jira.issue.comments.list" and exc.value.backend == "acli" and exc.value.hint and ran == ["/issue/A-1"]


async def test_the_transport_never_performs_http():
    t = OpTransport("acli", {})
    with pytest.raises(AssertionError, match="must not perform HTTP"):
        await t.client.get("https://example.atlassian.net/rest/api/3/myself")


async def test_declared_operations_are_reduced_to_what_can_actually_be_served():
    async def noop(spec):
        return {}

    t = OpTransport("acli", {"jira.user.search": noop, "jira.issue.get": noop})
    declared = frozenset({"jira.issue.get", "jira.issue.create", "jira.user.lookup_account_id", "export.jira.csv", "jira.agile.board.list"})
    b = Backend(t, (JiraRest(t),), declared)
    assert b.supported_operations() == {"jira.issue.get", "jira.user.lookup_account_id", "export.jira.csv"}      # create / board have no handler; lookup composes from search; csv is direct
    assert b.implementation_for(JiraIssueReader) is not None and all(p.kind is BackendKind.ACLI for p in b._parts)
    assert (await b.health()).status == "ready"
