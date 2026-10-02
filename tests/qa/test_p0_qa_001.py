from pathlib import Path

import pytest

from atlassian_client.protocols import JiraIssueReader, JiraAgileWriter
from tests.contract.conftest import BACKENDS

pytestmark = [pytest.mark.layer_01]

CONTRACT = Path(__file__).parents[1] / "contract"
P0_SUITES = ["JiraIssueReader", "JiraIssueSearcher", "JiraProjectReader", "JiraMetadataReader", "JiraUserDirectory", "JiraJqlTools",
             "JiraAgileReader", "JsmReader", "ConfluenceContentReader", "ConfluenceSearcher", "ConfluenceSpaceReader",
             "ConfluenceTemplateReader", "RawRequester", "IdentityReader"]


def test_harness_runs_every_suite_on_both_backends():
    assert BACKENDS == ["fake", "rest"]


def test_one_contract_file_per_p0_protocol():
    for name in P0_SUITES:
        assert (CONTRACT / f"test_{name.lower()}.py").exists(), name


def test_harness_fixture_gives_fake_and_rest_with_server_only_for_rest(harness):
    assert harness.name in ("fake", "rest")
    assert (harness.server is None) == (harness.name == "fake")
    assert harness.impl(JiraIssueReader) is not None


def test_harness_fails_loudly_when_a_backend_lacks_a_protocol(harness):
    if harness.name == "fake":
        pytest.skip("the fake implements everything")
    with pytest.raises(AssertionError, match="does not implement JiraAgileWriter"):
        harness.impl(JiraAgileWriter)                    # REST has no writers until P2


def test_every_contract_test_is_marked_layer_01():
    for f in CONTRACT.glob("test_*.py"):
        if f.name.startswith("test_matrix"):
            continue
        assert "layer_01" in f.read_text(), f.name
