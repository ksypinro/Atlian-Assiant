import pytest

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.protocols import (
    ConfluenceContentReader,
    ConfluenceSearcher,
    JiraAgileReader,
    JiraIssueReader,
    JiraIssueSearcher,
    JiraMetadataReader,
    JiraProjectReader,
    JiraUserDirectory,
    JsmReader,
)
from atlian_assiant.testing import FakeBackend, scenario
from atlian_assiant.testing.server import ScenarioServer

pytestmark = [pytest.mark.layer_02]


def dump(x):
    """Normalize results for comparison: pydantic models/pages/lists to plain data without source/raw/cursors."""
    if isinstance(x, list):
        return [dump(i) for i in x]
    if hasattr(x, "items") and hasattr(x, "next_cursor"):
        return {"items": dump(x.items), "more": x.has_more}
    if hasattr(x, "model_dump"):
        return x.model_dump(exclude={"source"}, mode="json")
    return x


@pytest.mark.anyio
async def test_fake_and_rest_return_identical_normalized_data_for_a_battery_of_reads():
    fake = FakeBackend()
    rest = ClientFactory().backend(BackendKind.REST, AtlassianConfig(site="example.atlassian.net", email="a@b.c", api_token="tok-123456"),
                                   http=ScenarioServer().client())
    cases = [
        (JiraIssueReader, "get_issue", ("PROJ-1",), {}), (JiraIssueReader, "list_comments", ("PROJ-1",), {"limit": 2}),
        (JiraIssueReader, "list_changelog", ("PROJ-1",), {}), (JiraIssueReader, "list_transitions", ("PROJ-1",), {}),
        (JiraIssueReader, "list_attachments", ("PROJ-1",), {}), (JiraIssueReader, "list_links", ("PROJ-1",), {}),
        (JiraIssueSearcher, "search_issues", ("x",), {"limit": 2}), (JiraProjectReader, "list_projects", (), {}),
        (JiraProjectReader, "get_project", ("PROJ",), {}), (JiraMetadataReader, "list_fields", (), {}),
        (JiraMetadataReader, "list_issue_types", (), {}), (JiraMetadataReader, "get_create_fields", ("PROJ", "Bug"), {}),
        (JiraUserDirectory, "myself", (), {}), (JiraUserDirectory, "search_users", ("bob",), {}),
        (JiraAgileReader, "list_boards", (), {}), (JiraAgileReader, "list_sprints", (42,), {}), (JiraAgileReader, "list_epics", (42,), {}),
        (JsmReader, "list_requests", (), {}), (JsmReader, "get_request_sla", ("OPS-1",), {}),
        (ConfluenceContentReader, "get_content", ("123",), {}), (ConfluenceContentReader, "list_versions", ("123",), {}),
        (ConfluenceContentReader, "list_labels", ("123",), {}), (ConfluenceContentReader, "get_restrictions", ("123",), {}),
        (ConfluenceSearcher, "search_cql", ("x",), {"limit": 2}),
    ]
    from atlian_assiant.protocols.base import implementation_of

    for proto, method, args, kwargs in cases:
        a = dump(await getattr(implementation_of(fake, proto), method)(*args, **kwargs))
        b = dump(await getattr(implementation_of(rest, proto), method)(*args, **kwargs))
        assert a == b, f"{proto.__name__}.{method} differs between fake and REST:\n{a}\n!=\n{b}"


@pytest.mark.anyio
async def test_fake_writers_mutate_state_so_downstream_tests_can_assert_on_them():
    from atlian_assiant.models.jira import IssueDraft, IssueUpdate
    from atlian_assiant.protocols import JiraIssueDeleter, JiraIssueWriter

    fb = FakeBackend()
    w = fb.implementation_for(JiraIssueWriter)
    created = await w.create_issue(IssueDraft(project="PROJ", issue_type="Bug", summary="New"))
    assert (await fb.jira.get_issue(created.key)).summary == "New"
    await w.edit_issue(created.key, IssueUpdate(summary="Edited", labels_add=["x"]))
    assert (await fb.jira.get_issue(created.key)).labels == ["x"]
    c = await w.add_comment(created.key, "hello")
    assert [x.body_markdown for x in (await fb.jira.list_comments(created.key)).items] == ["hello"] and c.id
    await fb.implementation_for(JiraIssueDeleter).delete_issue(created.key)
    from atlian_assiant.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await fb.jira.get_issue(created.key)


@pytest.mark.anyio
async def test_fake_confluence_update_is_version_checked():
    from atlian_assiant.core.errors import ConflictError
    from atlian_assiant.models.confluence import ContentUpdate

    fb = FakeBackend()
    updated = await fb.confluence.update_content("123", ContentUpdate(body="new", version=3))
    assert updated.version == 4
    with pytest.raises(ConflictError):
        await fb.confluence.update_content("123", ContentUpdate(body="stale", version=3))


def test_scenario_is_a_fresh_copy_each_time():
    a, b = scenario(), scenario()
    a["jira"]["issues"].clear()
    assert len(b["jira"]["issues"]) == 3
