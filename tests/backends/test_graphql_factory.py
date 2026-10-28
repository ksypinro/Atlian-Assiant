"""GRAPHQL backend: factory availability, health with live-schema validation, stored queries (design 10.4, 12.1)."""
import pytest

from atlian_assiant import AtlassianConfig, BackendKind
from atlian_assiant.backends.graphql import PARTIAL_OPS, GraphQLBackend, GraphQLBackendFactory
from atlian_assiant.backends.graphql.documents import load_documents
from atlian_assiant.backends.graphql.ops import BINDINGS
from atlian_assiant.capabilities import Deployment
from atlian_assiant.core.errors import (
    BackendUnavailableError,
    InvalidRequestError,
    NotFoundError,
    UnsupportedOperationError,
)
from atlian_assiant.factory import default_registry
from atlian_assiant.matrix import declared_operations
from atlian_assiant.models.jira import IssueDraft
from atlian_assiant.operations import CATALOG
from atlian_assiant.protocols import JiraIssueReader, JiraIssueWriter
from atlian_assiant.testing.gqlsim import FakeGraphQL
from atlian_assiant.testing.server import ScenarioServer

from ..contract.matrix_backends import graphql_ctx

CFG = dict(site="example.atlassian.net", email="a@b.c", api_token="tok-123456", cloud_id="cid-1")


def test_registered_cloud_only_and_precise_about_why_it_is_unavailable():
    f = default_registry().get(BackendKind.GRAPHQL)
    assert isinstance(f, GraphQLBackendFactory) and f.deployments == frozenset({Deployment.CLOUD})
    assert "no Cloud credentials" in f.availability(AtlassianConfig()).reason
    assert "ATLASSIAN_SITE" in f.availability(AtlassianConfig(email="a@b.c", api_token="tok-123456")).reason
    assert f.availability(AtlassianConfig(**CFG)).ok
    with pytest.raises(BackendUnavailableError):
        f.create(AtlassianConfig(), core=None)


def test_every_stored_document_has_a_binding_and_every_binding_a_document_and_catalog_operation():
    docs = load_documents()
    assert {b.document for b in BINDINGS} == set(docs)
    for d in docs.values():
        assert d.op in CATALOG and d.requires and d.text.lstrip().startswith("query "), d.name
    assert {b.op for b in BINDINGS} <= set(CATALOG)
    assert {d.name for d in docs.values() if d.experimental} == {"JiraIssueSearch", "JiraIssueCount", "JiraProject", "ConfPageComments"}


def test_stored_documents_validate_against_the_recorded_live_schema_subset():
    pytest.importorskip("graphql")
    from tools.gql_schema_subset import check

    assert check() == 0


def test_the_recorded_schema_subset_catches_a_guessed_field():
    graphql = pytest.importorskip("graphql")
    from tools.gql_schema_subset import SUBSET

    schema = graphql.build_schema(SUBSET.read_text(encoding="utf-8"))
    errors = graphql.validate(schema, graphql.parse("query Q($cloudId: ID!) { jira { issueByKey(key: \"A-1\", cloudId: $cloudId) { transitions { id } } } }"))
    assert errors and "transitions" in errors[0].message


def test_supported_operations_equal_the_declared_cells_and_the_executor_is_one_of_them():
    backend = graphql_ctx().backend
    assert isinstance(backend, GraphQLBackend) and backend.supported_operations() == declared_operations(BackendKind.GRAPHQL, PARTIAL_OPS)
    assert "graph.graphql.execute" in backend.supported_operations() and "jira.issue.delete" not in backend.supported_operations()


@pytest.mark.anyio
async def test_health_validates_stored_queries_against_the_live_schema_one_type_per_request():
    ctx = graphql_ctx()
    health = await ctx.backend.health()
    assert health.status == "ready" and "validated" in health.detail
    intro = [c for c in ctx.fake.calls if c["operationName"] == "Introspect"]
    assert intro and all(c["query"].count("__type") == 1 for c in intro)


@pytest.mark.anyio
async def test_a_missing_or_deprecated_field_marks_only_the_dependent_operations_unsupported():
    ctx = graphql_ctx()
    ctx.fake.missing, ctx.fake.deprecated = {"JiraQuery.issueByKey"}, {"JiraQuery.allJiraProjects"}
    health = await ctx.backend.health()
    assert health.status == "degraded"
    ops = ctx.backend.supported_operations()
    assert "jira.issue.get" not in ops and "jira.issue.comments.list" not in ops and "jira.project.list" not in ops
    assert "jira.issue.search" in ops and "confluence.content.get" in ops
    assert "missing from the live schema" in ctx.backend.invalid["jira.issue.get"] and "deprecated" in ctx.backend.invalid["jira.project.list"]
    before = len(ctx.fake.calls)
    with pytest.raises(UnsupportedOperationError):
        await ctx.backend.implementation_for(JiraIssueReader).get_issue("PROJ-1")
    assert len(ctx.fake.calls) == before                                  # refused locally: nothing sent


@pytest.mark.anyio
async def test_stored_operations_send_their_variables_and_the_beta_opt_in_headers_they_need():
    from atlian_assiant.protocols import JiraIssueSearcher

    ctx = graphql_ctx()
    await ctx.backend.implementation_for(JiraIssueReader).get_issue("PROJ-1")
    call = ctx.fake.calls[-1]
    assert call["operationName"] == "JiraIssueGet" and call["variables"] == {"cloudId": "cid-1", "key": "PROJ-1"} and "x-experimentalapi" not in call["headers"]
    page = await ctx.backend.implementation_for(JiraIssueSearcher).search_issues("project = PROJ", limit=1)
    assert ctx.fake.calls[-1]["headers"]["x-experimentalapi"] == "JiraIssueSearch" and page.items and page.next_cursor
    nxt = await ctx.backend.implementation_for(JiraIssueSearcher).search_issues("project = PROJ", limit=1, cursor=page.next_cursor)
    assert nxt.items[0].key != page.items[0].key and ctx.fake.calls[-1]["variables"]["after"]          # real cursor paging


@pytest.mark.anyio
async def test_graphql_errors_become_typed_errors_and_writes_are_left_to_rest():
    ctx = graphql_ctx()
    with pytest.raises(NotFoundError):
        await ctx.backend.implementation_for(JiraIssueReader).get_issue("PROJ-999")
    with pytest.raises(UnsupportedOperationError):                       # no stored mutations: the gateway's write fields were not verified
        await ctx.backend.implementation_for(JiraIssueWriter).create_issue(IssueDraft(project="PROJ", issue_type="Task", summary="x"))


@pytest.mark.anyio
async def test_a_beta_field_without_its_opt_in_header_is_a_clear_error():
    ctx = graphql_ctx()
    doc = load_documents()["JiraIssueCount"]
    with pytest.raises(InvalidRequestError, match="X-ExperimentalApi"):
        await ctx.backend.client.run(doc.text, {"cloudId": "cid-1", "jql": "project = PROJ"}, operation_name=doc.name)


def test_fake_gateway_serves_introspection_from_the_documents():
    schema = FakeGraphQL(ScenarioServer()).introspection()["__schema"]["types"]
    assert {"Query", "JiraQuery", "ConfluenceQueryApi"} <= {t["name"] for t in schema}
