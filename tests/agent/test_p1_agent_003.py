import re
from pathlib import Path

import pytest

from atlian_assiant import AsyncAtlassianClient, AtlassianClient, BackendKind, ClientFactory, Preset
from atlian_assiant.capabilities import Access
from atlian_assiant.routing import SelectionPolicy

GUIDE = Path(__file__).parents[2] / "atlian_assiant" / "agent" / "docs" / "AGENT_GUIDE.md"
LLMS = GUIDE.with_name("llms.txt")


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_SITE", "example.atlassian.net")
    monkeypatch.setenv("ATLASSIAN_EMAIL", "a@b.c")
    monkeypatch.setenv("ATLASSIAN_API_TOKEN", "tok-123456")


def test_for_agent_is_read_only_agent_preset_with_mcp_first_routing(env):
    c = AsyncAtlassianClient.for_agent()
    assert c.preset is Preset.AGENT and c.policy.allowed == frozenset({Access.READ, Access.SEARCH}) and not c.policy.allow_credit_consuming
    assert SelectionPolicy.from_preset(c.preset).order_for(__import__("atlian_assiant").operations.JIRA_ISSUE_GET)[0] is BackendKind.MCP
    d = c.describe()
    assert d["preset"] == "agent" and d["access"] == ["read", "search"] and d["backends"]["rest"].startswith("ready")
    assert any("read-only" in n for n in d["notes"])


def test_for_agent_on_the_sync_client_and_factory_agree(env):
    atl = AtlassianClient.for_agent()
    try:
        assert atl.describe()["preset"] == "agent"
        names = {t["name"] for t in atl.tools.export("anthropic")}
        assert "jira_get_issue" in names and "atlassian_semantic_search" not in names        # credits are off by default
    finally:
        atl.close()
    assert ClientFactory.for_agent().preset is Preset.AGENT


def test_agent_guide_has_the_outline_of_design_section_16_5():
    text = GUIDE.read_text()
    for heading in ("Start here", "Pick a tool", "Pagination", "Writes", "Names vs ids", "Errors", "Safety", "JQL cheat sheet", "CQL cheat sheet",
                    "Recipes", "Things not to do"):
        assert re.search(rf"^## \d+\. {heading}", text, re.M), heading
    assert "atlassian_describe" in text and "dry_run" in text and "next_cursor" in text and "<atlassian-data>" in text
    for recipe in ("Summarize a sprint", "Triage unassigned bugs", "Find docs related to an issue", "Release notes", "support question"):
        assert recipe in text


def test_every_tool_named_in_the_guide_exists():
    from atlian_assiant.agent.tools import CURATED

    known = {s.name for s in CURATED}
    mentioned = set(re.findall(r"`((?:jira|jsm|confluence|atlassian|events|analytics|audit)_[a-z_]+)`", GUIDE.read_text()))
    assert mentioned and mentioned <= known, mentioned - known
    assert known <= mentioned, known - mentioned                                      # the table lists every tool


def test_llms_txt_indexes_the_docs_and_tools():
    text = LLMS.read_text()
    assert text.startswith("# AtlassianClient") and "AGENT_GUIDE.md" in text and "operations.md" in text and "jira_get_issue" in text
