import pytest

from tools import docgen

pytestmark = [pytest.mark.layer_15]


def test_committed_docs_are_up_to_date():
    assert docgen.check_docs() == []


def test_generation_is_deterministic():
    for fn in docgen.GENERATORS.values():
        assert fn() == fn()


def test_check_detects_drift(tmp_path, monkeypatch):
    stale = tmp_path / "AGENT_GUIDE.md"
    stale.write_text("old")
    monkeypatch.setattr(docgen, "GENERATORS", {stale: docgen.gen_agent_guide})
    monkeypatch.setattr(docgen, "ROOT", tmp_path)
    assert docgen.check_docs() == ["AGENT_GUIDE.md"]
    missing = tmp_path / "nope.md"
    monkeypatch.setattr(docgen, "GENERATORS", {missing: docgen.gen_llms})
    assert docgen.check_docs() == ["nope.md"]


def test_operations_matrix_reflects_the_backends():
    text = docgen.gen_operations()
    row = next(line for line in text.splitlines() if line.startswith("| `jira.issue.get`"))
    assert "| READ | yes | yes | `client.jira.issues.get` |" in row
    row = next(line for line in text.splitlines() if line.startswith("| `jira.jql.parse`"))
    assert "| yes | - |" in row                                              # REST only
    row = next(line for line in text.splitlines() if line.startswith("| `graph.context`"))
    assert "| - | yes |" in row and "credits" in row and "cloud-only" in row
    assert "operations are catalogued" in text


def test_delivery_methods_ledger_lists_all_49():
    text = docgen.gen_delivery_methods()
    assert sum(1 for line in text.splitlines() if line.startswith("| DM-")) == 49 and "DM-19" in text


def test_main_check_and_write(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(docgen, "GENERATORS", {tmp_path / "a.md": docgen.gen_llms})
    monkeypatch.setattr(docgen, "ROOT", tmp_path)
    assert docgen.main(["--check"]) == 1
    assert docgen.main(["--write"]) == 0 and docgen.main(["--check"]) == 0
    assert "docgen check: ok" in capsys.readouterr().out
