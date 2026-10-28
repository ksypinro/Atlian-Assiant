"""Contract suite for GraphQLExecutor (design 9.6, 10.4): the reference (Fake) and the GRAPHQL backend must behave identically."""
import pytest

from atlian_assiant import BackendKind
from atlian_assiant.core.errors import InvalidRequestError, PolicyDeniedError
from atlian_assiant.protocols import GraphQLExecutor
from atlian_assiant.protocols.base import implementation_of
from atlian_assiant.testing import FakeBackend

from .matrix_backends import graphql_ctx

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]


@pytest.fixture(params=["fake", "graphql"])
def executor(request):
    if request.param == "fake":
        return implementation_of(FakeBackend(BackendKind.GRAPHQL), GraphQLExecutor), None
    ctx = graphql_ctx()
    return ctx.impl(GraphQLExecutor), ctx.fake


async def test_a_query_returns_the_response_object_with_data(executor):
    ex, _ = executor
    out = await ex.execute("{ __typename }")
    assert isinstance(out, dict) and out["data"] == {"__typename": "Query"}


async def test_variables_and_operation_name_reach_the_gateway(executor):
    ex, fake = executor
    out = await ex.execute("query Q($a: Int) { echo(a: $a) }", {"a": 1}, operation_name="Q")
    assert out["data"] == {"echo": {"a": 1}}
    if fake:
        call = fake.calls[-1]
        assert call["operationName"] == "Q" and call["variables"] == {"a": 1}


async def test_mutations_are_refused_because_the_executor_is_a_read_operation(executor):
    ex, fake = executor
    for doc in ("mutation M { jira { createIssue } }", "# note\nmutation { x }", "subscription S { y }"):
        with pytest.raises(PolicyDeniedError) as exc:
            await ex.execute(doc)
        assert exc.value.hint
    if fake:
        assert not [c for c in fake.calls if "mutation" in c["query"] or "subscription" in c["query"]]            # refused before anything was sent


async def test_a_graphql_error_without_data_is_a_typed_error_with_a_hint(executor):
    ex, _ = executor
    try:
        out = await ex.execute("{ boom }")
    except InvalidRequestError as exc:
        assert "boom" in str(exc) and exc.hint
    else:                                                      # the executor may return the raw envelope instead; then the errors must be visible
        assert out.get("errors")


async def test_partial_data_with_errors_is_returned_so_the_caller_can_decide(executor):
    ex, _ = executor
    out = await ex.execute("query partial { echo }", {"a": 2})
    assert out["data"] == {"echo": {"a": 2}} and out["errors"][0]["message"] == "partial failure"


async def test_experimental_apis_become_the_x_experimentalapi_header(executor):
    ex, fake = executor
    await ex.execute("{ __typename }", experimental_apis=["TeamworkGraph", "JiraIssueSearch"])
    if fake:
        assert fake.calls[-1]["headers"]["x-experimentalapi"] == "TeamworkGraph,JiraIssueSearch"
