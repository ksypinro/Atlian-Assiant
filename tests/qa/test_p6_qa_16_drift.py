"""QA.16 (test layer 16: API drift): nightly codegen from OpenAPI, diff -> PR, operation-coverage report."""
import json
from pathlib import Path

import pytest

from tools import codegen

pytestmark = [pytest.mark.layer_16]
ROOT = Path(__file__).parents[2]


def test_a_changed_spec_is_detected_as_added_removed_and_changed_operations(tmp_path):
    for f in codegen.SPECS.glob("*.json"):
        (tmp_path / f.name).write_text(f.read_text())
    old = codegen.build_index(tmp_path)
    spec = json.loads((tmp_path / "jira-v3.json").read_text())
    del spec["paths"]["/rest/api/3/label"]
    spec["paths"]["/rest/api/3/issue/{issueIdOrKey}"]["get"]["parameters"].append({"name": "properties", "in": "query"})
    spec["paths"]["/rest/api/3/brandNew"] = {"get": {"operationId": "getBrandNew", "summary": "new", "tags": ["x"]}}
    (tmp_path / "jira-v3.json").write_text(json.dumps(spec))
    d = codegen.diff(old, codegen.build_index(tmp_path))
    assert d == {"added": ["api.jira.getBrandNew"], "removed": ["api.jira.getAllLabels"], "changed": ["api.jira.getIssue"]}


def test_the_check_fails_when_the_committed_index_lags_the_specs(tmp_path, monkeypatch, capsys):
    stale = tmp_path / "operations_index.json"
    stale.write_text(json.dumps({"generated_by": "x", "operations": []}))
    monkeypatch.setattr(codegen, "OUT", stale)
    assert codegen.main(["--check"]) == 1 and "STALE" in capsys.readouterr().out


def test_the_diff_command_exits_non_zero_when_something_drifted(tmp_path, capsys):
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"operations": []}))
    assert codegen.main(["--diff", str(old)]) == 1 and "added" in capsys.readouterr().out
    assert codegen.main(["--diff", str(codegen.OUT)]) == 0


def test_the_nightly_job_fetches_regenerates_checks_reachability_and_opens_a_pr():
    wf = (ROOT / ".github" / "workflows" / "api-drift.yml").read_text()
    for needle in ("schedule:", "cron:", "tools.codegen --fetch --write", "--check-reachability", "create-pull-request"):
        assert needle in wf
    sources = json.loads((ROOT / "specs" / "sources.json").read_text())
    assert set(sources) == set(codegen.APIS) and all(u.startswith("https://") for u in sources.values())
