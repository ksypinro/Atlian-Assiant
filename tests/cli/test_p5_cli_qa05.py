"""QA.05 (test layer 5: CLIs): fake binaries on PATH emitting fixture JSON, and probe tests across recorded ``--help`` outputs."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from atlian_assiant.backends.acli import AcliBackend
from atlian_assiant.backends.cli.command_maps import ACLI_MAP
from atlian_assiant.core.cli_runner import CliResult, CliRunner
from atlian_assiant.protocols import JiraIssueReader, JiraIssueSearcher

pytestmark = [pytest.mark.layer_05, pytest.mark.anyio]
FIXTURES = Path(__file__).parent / "fixtures"
ISSUE = {"id": "10001", "key": "PROJ-1", "fields": {"summary": "From a real process", "status": {"name": "Done", "statusCategory": {"name": "Done"}},
                                                     "project": {"id": "1", "key": "PROJ"}}}


def install_fake_binary(directory: Path, name: str = "acli") -> Path:
    """A real executable on PATH: prints fixture JSON for `jira workitem view`, and records its argv and stdin next to itself."""
    script = directory / name
    script.write_text(f"""#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
with open(os.path.join(os.path.dirname(__file__), "calls.log"), "a") as log:
    log.write(json.dumps({{"args": args, "env_has_token": "ATLASSIAN_API_TOKEN" in os.environ}}) + "\\n")
if args == ["--version"]:
    print("acli version 2.1.0")
elif args[-1] == "--help":
    print("Usage:\\n  acli " + " ".join(args[:-1]) + " [flags]\\n  --json\\n  --jql\\n  --fields\\n  --paginate\\n  --count\\n  --csv\\n  --key\\n  --id\\n  --yes")
elif args[:3] == ["jira", "workitem", "view"]:
    print(json.dumps({ISSUE!r}))
else:
    sys.stderr.write("issue does not exist or you do not have permission to see it")
    sys.exit(1)
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


async def test_a_real_process_on_path_is_run_with_an_argv_list_and_a_clean_environment(tmp_path, monkeypatch):
    install_fake_binary(tmp_path)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("ATLASSIAN_API_TOKEN", "super-secret-token")
    backend = AcliBackend(CliRunner("acli"))
    issue = await backend.implementation_for(JiraIssueReader).get_issue("PROJ-1")
    assert issue.summary == "From a real process" and issue.status.name == "Done"
    calls = [json.loads(line) for line in (tmp_path / "calls.log").read_text().splitlines()]
    assert calls[0]["args"] == ["--version"] and calls[0]["env_has_token"] is False      # the first real call probes once
    view = next(c for c in calls if c["args"][:3] == ["jira", "workitem", "view"] and c["args"][-1] != "--help")
    assert view["args"] == ["jira", "workitem", "view", "PROJ-1", "--fields", "*all", "--json"] and view["env_has_token"] is False


async def test_a_failing_real_process_surfaces_its_own_message_as_a_typed_error(tmp_path, monkeypatch):
    from atlian_assiant.core.errors import NotFoundError

    install_fake_binary(tmp_path)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(NotFoundError):
        await AcliBackend(CliRunner("acli")).implementation_for(JiraIssueSearcher).count_issues("project = PROJ")


async def test_health_probes_the_real_binary(tmp_path, monkeypatch):
    install_fake_binary(tmp_path)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    health = await AcliBackend(CliRunner("acli")).health()
    assert health.status == "ready" and "acli 2.1.0" in health.detail


RECORDED = FIXTURES / "acli_1.3.39"          # `acli <command> --help` of every command in the map, recorded from a live acli 1.3.39


def recorded_runner(version: str, search_help: str | None = None) -> CliRunner:
    """A runner that answers ``--version`` and every ``--help`` page from RECORDED outputs of a real acli; ``search_help`` swaps in another version's
    ``workitem search --help``. An unrecorded command answers like acli does: the parent's help, exit 0."""
    async def executor(argv, stdin, timeout, env):
        args = list(argv[1:])
        if args == ["--version"]:
            return CliResult(tuple(argv), 0, f"acli version {version}-stable")
        if args[-1] == "--help":
            if search_help and args == ["jira", "workitem", "search", "--help"]:
                return CliResult(tuple(argv), 0, (FIXTURES / search_help).read_text())
            page = RECORDED / ("_".join(args[:-1]) + ".txt")
            return CliResult(tuple(argv), 0, page.read_text() if page.exists() else "Usage:\n  acli jira [command]\n")
        return CliResult(tuple(argv), 1, "", "not found")
    return CliRunner("acli", executor=executor, which=lambda b: "/x/acli")


async def test_every_command_in_the_map_exists_with_its_flags_in_the_recorded_live_acli():
    report = await AcliBackend(recorded_runner("1.3.39")).probe()
    assert report.dropped == {} and report.available == ACLI_MAP.operations and report.version == "1.3.39"


async def test_an_unknown_subcommand_answered_with_the_parent_help_is_dropped():
    from atlian_assiant.backends.cli.commandmap import Command, CommandMap
    from atlian_assiant.backends.cli.probe import CapabilityProbe

    cmap = CommandMap("acli", [Command("jira.issue.transitions.list", ("jira", "workitem", "transition", "--key", "{key}", "--list", "--json"), requires=("--list",)),
                               Command("jira.meta.fields.list", ("jira", "field", "list", "--json"), requires=("--json",))])
    report = await CapabilityProbe(recorded_runner("1.3.39"), cmap).probe()
    assert report.available == frozenset() and "--list" in report.dropped["jira.issue.transitions.list"]   # the command exists, the flag does not
    assert "does not exist" in report.dropped["jira.meta.fields.list"]


async def test_probe_against_the_recorded_help_of_a_current_version_keeps_search_count_and_csv():
    report = await AcliBackend(recorded_runner("2.0.0", "acli_2.0_workitem_search_help.txt")).probe()
    assert {"jira.issue.search", "jira.issue.count", "export.jira.csv"} <= report.available and report.dropped == {}


async def test_probe_against_the_recorded_help_of_an_older_version_drops_exactly_what_it_lacks():
    backend = AcliBackend(recorded_runner("1.2.0", "acli_1.2_workitem_search_help.txt"))
    report = await backend.probe()
    assert set(report.dropped) == {"jira.issue.search", "jira.issue.count", "export.jira.csv"}
    assert "--paginate" in report.dropped["jira.issue.search"] and "--count" in report.dropped["jira.issue.count"] and "--csv" in report.dropped["export.jira.csv"]
    assert "jira.issue.get" in backend.supported_operations() and "jira.issue.search" not in backend.supported_operations()
