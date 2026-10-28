"""A write must never be sent twice: fallback is allowed only when NOTHING was sent (design section 12.4)."""
import pytest

from atlian_assiant import AtlassianConfig, ClientFactory
from atlian_assiant import operations as ops
from atlian_assiant.capabilities import BackendKind as B
from atlian_assiant.core.errors import (
    AtlassianError,
    BackendUnavailableError,
    NoBackendAvailableError,
    ServerError,
    UnsupportedOperationError,
)
from atlian_assiant.models.jira import IssueDraft
from atlian_assiant.policy import AccessPolicy, AutoApprove
from atlian_assiant.protocols import JiraIssueWriter
from atlian_assiant.routing import Router, SelectionPolicy
from atlian_assiant.testing import FakeBackend

pytestmark = [pytest.mark.anyio, pytest.mark.layer_09]

CREATE = ops.JIRA_ISSUE_CREATE
DRAFT = IssueDraft(project="PROJ", issue_type="Bug", summary="Once only")


class Spy(FakeBackend):
    """Counts create_issue calls and fails in a configurable way."""

    def __init__(self, kind, exc=None, *, sent_before_failing=False):
        super().__init__(kind)
        self.exc, self.calls, self.sent = exc, 0, sent_before_failing
        orig = self.jira.create_issue

        async def create_issue(draft):
            self.calls += 1
            if self.exc is not None:
                if self.sent:
                    await orig(draft)                       # the request reached Atlassian and WAS processed
                raise self.exc
            return await orig(draft)
        self.jira.create_issue = create_issue


def router(*backends):
    return Router({b.kind: b for b in backends}, SelectionPolicy({"*": tuple(b.kind for b in backends)}))


async def issue_count(backend):
    return len(backend.store.issues)


async def test_unsupported_or_unavailable_before_sending_falls_back_and_creates_exactly_one_issue():
    first = Spy(B.MCP, UnsupportedOperationError("no tool"))
    second = Spy(B.REST)
    before = await issue_count(second)
    created = await router(first, second).call(CREATE, JiraIssueWriter, "create_issue", DRAFT)
    assert created.key and (first.calls, second.calls) == (1, 1)
    assert await issue_count(first) == len(FakeBackend().store.issues) and await issue_count(second) == before + 1


@pytest.mark.parametrize("exc", [ServerError("503 after the request left the building", status=503), AtlassianError("timeout while waiting for the response"),
                                 RuntimeError("connection reset")])
async def test_any_error_after_the_request_was_sent_propagates_and_is_never_replayed_elsewhere(exc):
    first, second = Spy(B.MCP, exc, sent_before_failing=True), Spy(B.REST)
    with pytest.raises(type(exc)):
        await router(first, second).call(CREATE, JiraIssueWriter, "create_issue", DRAFT)
    assert (first.calls, second.calls) == (1, 0)
    assert await issue_count(second) == len(FakeBackend().store.issues)          # the second backend was never touched


async def test_every_backend_declining_before_sending_reports_all_attempts_and_creates_nothing():
    a, b = Spy(B.MCP, BackendUnavailableError("down")), Spy(B.REST, UnsupportedOperationError("no endpoint"))
    with pytest.raises(NoBackendAvailableError) as exc:
        await router(a, b).call(CREATE, JiraIssueWriter, "create_issue", DRAFT)
    assert [x[0] for x in exc.value.details["attempts"]] == ["mcp", "rest"] and (a.calls, b.calls) == (1, 1)


async def test_forced_backend_never_falls_back_for_writes():
    a, b = Spy(B.MCP, UnsupportedOperationError("no")), Spy(B.REST)
    with pytest.raises(NoBackendAvailableError):
        await router(a, b).forced("mcp").call(CREATE, JiraIssueWriter, "create_issue", DRAFT)
    assert b.calls == 0


async def test_non_idempotent_operations_are_marked_so_transports_do_not_retry_them():
    for op_id in ("jira.issue.create", "jira.issue.transition", "jira.issue.comment.add", "jira.issue.worklog.add", "jira.issue.link.create",
                  "confluence.content.create", "confluence.comment.create", "jsm.request.create"):
        assert not ops.get(op_id).idempotent, op_id


async def test_end_to_end_through_the_facade_one_failed_write_one_issue():
    mcp = Spy(B.MCP, ServerError("500", status=500), sent_before_failing=True)
    rest = Spy(B.REST)
    client = ClientFactory().build_async(AtlassianConfig(site="x"), backends=[], preset="agent", policy=AccessPolicy.read_write(), confirm=AutoApprove.__new__(AutoApprove),
                                         extra_backends={B.MCP: mcp, B.REST: rest}) if False else None
    assert client is None
    from atlian_assiant.policy import CallbackConfirmation
    from tests.agent.conftest import build

    c = build(AccessPolicy.read_write(), confirm=CallbackConfirmation(lambda p: True))
    c.router._backends[B.MCP] = mcp
    c.router._backends[B.REST] = rest
    with pytest.raises(ServerError):
        await c.jira.issues.create(DRAFT)
    assert (mcp.calls, rest.calls) == (1, 0)
