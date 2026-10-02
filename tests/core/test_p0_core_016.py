import inspect

import pytest

from atlassian_client import AtlassianConfig, BackendKind, ClientFactory
from atlassian_client.agent.describe import walk_namespaces
from atlassian_client.core.errors import NoBackendAvailableError, UnsupportedOperationError
from atlassian_client.protocols import (ConfluenceContentReader, ConfluenceSearcher, ConfluenceSpaceReader, ConfluenceTemplateReader,
                                        JiraAgileReader, JiraIssueReader, JiraIssueSearcher, JiraJqlTools, JiraMetadataReader,
                                        JiraProjectReader, JiraUserDirectory, JsmReader, RawRequester, SemanticSearcher,
                                        TeamworkGraphReader)
from atlassian_client.testing import FakeBackend
from tools import docgen


def make():
    return ClientFactory().build_async(AtlassianConfig(site="example.atlassian.net"), backends=[],
                                       extra_backends={BackendKind.REST: FakeBackend()})


def test_namespace_tree_of_section_13_1():
    c = make()
    paths = {p for p, _ in walk_namespaces(c)}
    for expected in ("jira", "jira.issues", "jira.projects", "jira.metadata", "jira.users", "jira.filters", "jira.dashboards", "jira.agile",
                     "jira.jql", "jira.expressions", "jsm", "confluence", "confluence.content", "confluence.pages", "confluence.search",
                     "confluence.spaces", "confluence.comments", "confluence.attachments", "confluence.labels", "confluence.properties",
                     "confluence.tasks", "confluence.permissions", "confluence.templates", "raw"):
        assert expected in paths, expected
    for name in ("describe", "help", "via", "with_policy", "whoami", "aclose", "from_env"):
        assert callable(getattr(c, name)), name


def test_every_public_method_follows_the_docstring_standard():
    assert docgen.lint_namespace_tree(make()) == []


@pytest.mark.anyio
async def test_documented_examples_are_executable_against_the_fake():
    assert await docgen.run_all_examples(make()) == []


def test_positional_parameters_match_the_protocol_they_route_to():
    """Facade signatures must stay in step with the protocols: same positional names, same keyword-only names."""
    import atlassian_client.protocols as P

    protos = {n: getattr(P, n) for n in P.__all__ if hasattr(getattr(P, n), '_is_protocol')}
    checked = 0
    for path, ns in walk_namespaces(make()):
        for op_id, method_name in ns._OPS.items():
            src = inspect.getsource(getattr(type(ns), method_name))
            proto_name, proto_method = src.split('self._call("' + op_id + '", ')[1].split(", ")[0], src.split('self._call("' + op_id + '", ')[1].split('"')[1]
            sig_f = inspect.signature(getattr(ns, method_name))
            sig_p = inspect.signature(getattr(protos[proto_name], proto_method))
            expand = [x for x, p in sig_p.parameters.items() if p.kind is p.VAR_KEYWORD]
            pos = lambda s: [n for n, p in s.parameters.items() if p.kind is p.POSITIONAL_OR_KEYWORD and n != "self"] + ([] if s is sig_f or not expand else expand)  # noqa: E731
            kw = lambda s: {n for n, p in s.parameters.items() if p.kind is p.KEYWORD_ONLY and n not in ("backend", "dry_run")}  # noqa: E731
            assert pos(sig_f) == pos(sig_p), f"{path}.{method_name}: positional parameters differ from {proto_name}.{proto_method}"
            assert kw(sig_f) == kw(sig_p), f"{path}.{method_name}: keyword parameters differ from {proto_name}.{proto_method}"
            checked += 1
    assert checked >= 140


@pytest.mark.anyio
async def test_facade_routes_and_stamps_source_and_via_forces_a_backend():
    c = make()
    issue = await c.jira.issues.get("PROJ-1")
    assert issue.key == "PROJ-1" and issue.source.backend is BackendKind.REST
    assert (await c.via("rest").jira.issues.get("PROJ-1")).key == "PROJ-1"
    with pytest.raises(NoBackendAvailableError):
        await c.via("mcp").jira.issues.get("PROJ-1")
    assert (await c.whoami()).account_id == "acc-ann"


@pytest.mark.anyio
async def test_iteration_helpers_and_typed_shortcuts():
    c = make()
    assert [i.key async for i in c.jira.issues.iter_search("x", page_size=2)] == ["PROJ-1", "PROJ-2", "PROJ-3"]
    assert [i.key async for i in c.jira.issues.iter_search("x", page_size=2, max_items=1)] == ["PROJ-1"]
    assert [h.content_id async for h in c.confluence.search.iter_cql("x", page_size=2)] == ["123", "124", "125"]
    assert (await c.confluence.pages.get("123")).title == "Runbook"
    hydrated = await c.confluence.search.search_then_hydrate("x", limit=2)
    assert [p.id for p in hydrated] == ["123", "124"]
    assert (await c.jira.jql.validate("project = PROJ")).valid
    from atlassian_client.core.errors import InvalidRequestError
    with pytest.raises(InvalidRequestError) as exc:
        await c.jira.jql.validate("BROKEN (((")
    assert exc.value.details["errors"]


def test_planned_namespaces_fail_loudly_and_name_the_phase():
    c = make()
    for ns, phase in ((c.webhooks, "P3"), (c.events, "P3"), (c.analytics, "P5"), (c.mirror, "P6")):
        with pytest.raises(UnsupportedOperationError) as exc:
            ns.anything
        assert phase in str(exc.value) and "TRACKING.md" in exc.value.hint
