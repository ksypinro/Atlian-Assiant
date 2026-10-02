import inspect

import pytest

from atlassian_client import AtlassianClient, AtlassianConfig, BackendKind, ClientFactory
from atlassian_client.facade.sync import SyncProxy
from atlassian_client.testing import FakeBackend


@pytest.fixture()
def atl():
    client = AtlassianClient(ClientFactory().build_async(AtlassianConfig(site="example.atlassian.net"), backends=[],
                                                          extra_backends={BackendKind.REST: FakeBackend()}))
    yield client
    client.close()


def test_coroutines_become_blocking_calls(atl):
    issue = atl.jira.issues.get("PROJ-1")
    assert issue.key == "PROJ-1"
    assert atl.confluence.content.get("123").title == "Runbook"
    assert atl.jira.issues.search("x", limit=2).next_cursor


def test_async_iterators_become_iterators(atl):
    keys = [i.key for i in atl.jira.issues.iter_search("x", page_size=2)]
    assert keys == ["PROJ-1", "PROJ-2", "PROJ-3"]
    assert inspect.isgenerator(atl.jira.issues.iter_search("x"))


def test_docstrings_and_names_are_preserved(atl):
    get = atl.jira.issues.get
    assert get.__name__ == "get" and "Fetch one Jira issue" in get.__doc__ and "When to use" in get.__doc__


def test_namespaces_are_wrapped_and_plain_methods_pass_through(atl):
    assert isinstance(atl.jira, SyncProxy) and isinstance(atl.jira.issues, SyncProxy)
    assert atl.describe()["preset"] == "service"                    # synchronous method returns directly
    assert "jira.issue.get" in atl.help("jira.issue.get")
    assert atl.via("rest").jira.issues.get("PROJ-2").key == "PROJ-2"


def test_errors_propagate_with_their_type(atl):
    from atlassian_client.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        atl.jira.issues.get("PROJ-999")


def test_context_manager_closes_once():
    with AtlassianClient(ClientFactory().build_async(AtlassianConfig(site="x"), backends=[], extra_backends={BackendKind.REST: FakeBackend()})) as c:
        assert c.jira.issues.get("PROJ-1").key == "PROJ-1"
    c.close()                                                       # idempotent
