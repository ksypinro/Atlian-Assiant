"""P5.CLI.001: CliRunner, CapabilityProbe and the command maps (design 10.5-10.7; test layer 5: fake binaries emitting fixture JSON)."""
from __future__ import annotations

import json
import sys

import pytest

from atlian_assiant.backends.acli import AcliBackend
from atlian_assiant.backends.cli.command_maps import ACLI_MAP, JIRA_CLI_MAP, TWG_MAP
from atlian_assiant.backends.cli.commandmap import Command, CommandMap, shape, unshape
from atlian_assiant.backends.jira_cli import JiraCliBackend
from atlian_assiant.backends.twg import TwgBackend
from atlian_assiant.core.cli_runner import CliResult, CliRunner
from atlian_assiant.core.errors import (
    AuthenticationError,
    BackendUnavailableError,
    CliExecutionError,
    InvalidRequestError,
    NotFoundError,
    PermissionDeniedError,
    UnsupportedOperationError,
)
from atlian_assiant.core.http import RequestSpec
from atlian_assiant.core.urls import ApiSurface
from atlian_assiant.models.jira import IssueDraft, IssueUpdate
from atlian_assiant.operations import CATALOG
from atlian_assiant.protocols import JiraIssueReader, JiraIssueSearcher, JiraIssueWriter
from atlian_assiant.testing.clisim import FakeCli
from atlian_assiant.testing.server import ScenarioServer

pytestmark = [pytest.mark.layer_05, pytest.mark.anyio]
MAPS = {"acli": ACLI_MAP, "twg": TWG_MAP, "jira_cli": JIRA_CLI_MAP}


# ---------------------------------------------------------------------------------------------------------------- CliRunner
async def test_runner_runs_an_argv_list_without_a_shell_and_filters_the_environment(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_API_TOKEN", "super-secret-token")
    monkeypatch.setenv("HOME", "/home/x")
    seen = {}

    async def executor(argv, stdin, timeout, env):
        seen.update(argv=list(argv), env=dict(env), stdin=stdin, timeout=timeout)
        return CliResult(tuple(argv), 0, "ok")
    runner = CliRunner("acli", executor=executor, which=lambda b: "/bin/acli", extra_env={"ACLI_PROFILE": "ci"}, timeout=7)
    out = await runner.run(["jira", "workitem", "view", "A; rm -rf /"], stdin="{}")
    assert out.stdout == "ok" and seen["argv"] == ["acli", "jira", "workitem", "view", "A; rm -rf /"] and seen["stdin"] == "{}" and seen["timeout"] == 7
    assert "ATLASSIAN_API_TOKEN" not in seen["env"] and seen["env"]["HOME"] == "/home/x" and seen["env"]["ACLI_PROFILE"] == "ci"


async def test_runner_really_spawns_a_process_with_a_clean_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLASSIAN_API_TOKEN", "super-secret-token")
    script = tmp_path / "probe.py"
    script.write_text("import os, sys\nprint(sorted(k for k in os.environ if 'TOKEN' in k), sys.stdin.read())\n")
    runner = CliRunner(sys.executable)
    out = await runner.run([str(script)], stdin="piped")
    assert out.stdout.strip() == "[] piped"


async def test_runner_maps_failures_to_typed_errors_with_hints():
    def runner_for(stderr: str, code: int = 1) -> CliRunner:
        async def ex(argv, stdin, timeout, env):
            return CliResult(tuple(argv), code, "", stderr)
        return CliRunner("acli", executor=ex, which=lambda b: "/bin/acli")
    with pytest.raises(AuthenticationError) as a:
        await runner_for("error: not logged in").run(["jira"])
    assert "human" in a.value.hint
    with pytest.raises(NotFoundError):
        await runner_for("issue does not exist or you do not have permission to see it").run(["jira"])
    with pytest.raises(PermissionDeniedError):
        await runner_for("403 forbidden").run(["jira"])
    with pytest.raises(CliExecutionError) as e:
        await runner_for("segfault", 139).run(["jira", "workitem", "view"])
    assert "139" in str(e.value) and e.value.hint
    assert (await runner_for("anything", 3).run(["x"], check=False)).returncode == 3


async def test_runner_times_out_and_reports_a_missing_binary_without_running_anything():
    async def slow(argv, stdin, timeout, env):
        raise TimeoutError
    with pytest.raises(CliExecutionError) as t:
        await CliRunner("acli", executor=slow, which=lambda b: "/bin/acli", timeout=1).run(["jira"])
    assert "timed out" in str(t.value) and t.value.retryable
    ran = []

    async def never(argv, stdin, timeout, env):
        ran.append(argv)
        return CliResult(tuple(argv), 0)
    with pytest.raises(BackendUnavailableError) as m:
        await CliRunner("acli", executor=never, which=lambda b: None).run(["jira"])
    assert "not installed" in str(m.value) and not ran


# ---------------------------------------------------------------------------------------------------------------- command maps
@pytest.mark.parametrize("tool", MAPS)
def test_every_mapped_operation_is_a_catalog_operation_and_no_command_is_duplicated(tool):
    cmap = MAPS[tool]
    assert cmap.operations <= set(CATALOG), sorted(cmap.operations - set(CATALOG))
    keys = [(c.op, c.path) for c in cmap.all]
    assert len(keys) == len(set(keys))
    argvs = [tuple(c.argv) for c in cmap.all]
    assert len(argvs) == len(set(argvs)), "two commands share an argv: the inverse parse would be ambiguous"


@pytest.mark.parametrize("tool", MAPS)
def test_all_flag_guesses_are_declared_via_requires_so_the_probe_can_drop_them(tool):
    for c in MAPS[tool].all:
        flags = {t if isinstance(t, str) else t[0] for t in c.argv if (t if isinstance(t, str) else t[0]).startswith("-")}
        global_flags = {"-o", "--output-summary"} if tool == "twg" else set()       # twg's global options live in `twg --help`, not per command
        assert flags <= set(c.requires) | {"--json", "--raw", "--output", "--yes", "--from-json"} | global_flags or c.requires, f"{c.op}: flags {sorted(flags)} are not probed"


def test_render_and_parse_are_exact_inverses_including_optional_flags_and_json_variables():
    cmap = JIRA_CLI_MAP
    spec = RequestSpec("POST", ApiSurface.JIRA_PLATFORM, "/issue", json={"fields": {"project": {"key": "PROJ"}, "issuetype": {"name": "Task"}, "summary": "S x",
                                                                                  "labels": ["a", "b"], "assignee": {"accountId": "acc"}}}, operation="jira.issue.create")
    cmd = cmap.select("jira.issue.create", "/issue")
    argv, stdin = cmap.render(cmd, spec)
    assert argv == ["issue", "create", "-p", "PROJ", "-t", "Task", "-s", "S x", "-a", "acc", "-l", "a,b", "--no-input", "--raw"] and stdin is None
    parsed_cmd, parsed, _ = cmap.parse(argv, stdin)
    assert parsed_cmd.op == "jira.issue.create"
    assert parsed.json["fields"]["labels"] == ["a", "b"] and parsed.json["fields"]["issuetype"] == {"name": "Task"} and parsed.json["fields"]["assignee"] == {"accountId": "acc"}


def test_strict_commands_refuse_what_the_flags_cannot_express_instead_of_dropping_it():
    cmap = JIRA_CLI_MAP
    spec = RequestSpec("POST", ApiSurface.JIRA_PLATFORM, "/issue", json={"fields": {"project": {"key": "P"}, "issuetype": {"name": "Task"}, "summary": "s",
                                                                                  "customfield_10016": 5}}, operation="jira.issue.create")
    with pytest.raises(UnsupportedOperationError) as exc:
        cmap.render(cmap.select("jira.issue.create", "/issue"), spec)
    assert "customfield_10016" in str(exc.value) and "REST" in exc.value.hint
    bare = Command("x", ("a", "{missing}"))
    with pytest.raises(InvalidRequestError) as missing:
        CommandMap("t", [bare]).render(bare, RequestSpec("GET", ApiSurface.JIRA_PLATFORM, "/", operation="x"))
    assert "missing" in str(missing.value)


def test_wrap_shapes_a_bare_cli_list_into_every_pagination_envelope_and_unwrap_inverts_it():
    items = [{"id": i} for i in range(5)]
    offset = shape("offset:values", items, RequestSpec("GET", ApiSurface.JIRA_AGILE, "/board", {"startAt": 2, "maxResults": 2}))
    assert offset["values"] == items[2:4] and offset["total"] == 5 and offset["isLast"] is False
    token = shape("token:issues", items, RequestSpec("POST", ApiSurface.JIRA_PLATFORM, "/search/jql", json={"maxResults": 2, "nextPageToken": "2"}))
    assert token["issues"] == items[2:4] and token["nextPageToken"] == "4"
    last = shape("token:issues", items, RequestSpec("POST", ApiSurface.JIRA_PLATFORM, "/search/jql", json={"maxResults": 2, "nextPageToken": "4"}))
    assert last["nextPageToken"] is None and last["isLast"] is True
    assert shape("jsm:values", items, RequestSpec("GET", ApiSurface.JSM, "/x", {"start": 0, "limit": 3}))["isLastPage"] is False
    assert shape("cql:results", items, RequestSpec("GET", ApiSurface.CONFLUENCE_V1, "/search", {"start": 0, "limit": 2}))["_links"] == {"next": "more"}
    link = shape("link:results", items, RequestSpec("GET", ApiSurface.CONFLUENCE_V2, "/pages", {"limit": 2}))
    assert link["_links"]["next"] == "/wiki/api/v2/pages?cursor=2&limit=2"
    for w, wrapped in (("offset:values", offset), ("token:issues", token), ("jsm:values", shape("jsm:values", items, RequestSpec("GET", ApiSurface.JSM, "/x", {})))):
        assert unshape(w, wrapped) == wrapped[w.partition(":")[2] if w.partition(":")[2] in wrapped else "values"]


# ---------------------------------------------------------------------------------------------------------------- CapabilityProbe
def acli(**fake_kw):
    server = ScenarioServer()
    fake = FakeCli(ACLI_MAP, server, **fake_kw)
    return AcliBackend(fake.runner()), fake, server


async def test_probe_runs_version_and_help_once_caches_and_keeps_everything_when_flags_exist():
    backend, fake, _ = acli()
    report = await backend.probe()
    assert report.version == "2.0.0" and report.dropped == {} and report.available == ACLI_MAP.operations
    calls = len(fake.calls)
    assert fake.calls[0] == ("--version",) and any(c[-1] == "--help" for c in fake.calls)
    assert await backend.probe() is report and len(fake.calls) == calls                     # cached
    assert len({c for c in fake.calls if c[-1:] == ("--help",)}) == len([c for c in fake.calls if c[-1:] == ("--help",)]), "each help page is read once"


async def test_probe_drops_operations_whose_flags_the_installed_version_lacks_and_they_then_refuse_cleanly():
    backend, fake, server = acli(missing_flags=["--paginate"])
    before = backend.supported_operations()
    assert "jira.issue.search" in before
    report = await backend.probe()
    assert "jira.issue.search" in report.dropped and "--paginate" in report.dropped["jira.issue.search"]
    assert "jira.issue.search" not in backend.supported_operations() and "jira.issue.get" in backend.supported_operations()
    n_calls = len(fake.calls)
    with pytest.raises(UnsupportedOperationError):
        await backend.implementation_for(JiraIssueSearcher).search_issues("project = PROJ")
    assert len(fake.calls) == n_calls and not server.requests                                # nothing was executed


async def test_probe_drops_operations_of_a_command_that_does_not_exist_in_this_version():
    backend, _, _ = acli(missing_prefixes=[("jira", "workitem", "attachment", "list"), ("jira", "workitem", "list-watchers")])
    report = await backend.probe()
    assert {"jira.issue.attachments.list", "jira.issue.watchers.list"} <= set(report.dropped) and "jira.issue.get" in report.available
    assert "does not exist" in report.dropped["jira.issue.attachments.list"]
    assert "jira.issue.watchers.list" not in backend.cli_transport.operations and "jira.issue.get" in backend.supported_operations()


async def test_third_party_cli_below_the_pinned_version_is_unavailable_and_health_says_so():
    server = ScenarioServer()
    old = JiraCliBackend(FakeCli(JIRA_CLI_MAP, server, version="1.4.0").runner())
    health = await old.health()
    assert health.status == "unavailable" and "older than the minimum 1.5.0" in health.detail
    good = JiraCliBackend(FakeCli(JIRA_CLI_MAP, server, version="1.7.1").runner())
    assert (await good.health()).status == "ready" and "third-party" in (await good.health()).detail
    assert any("third-party" in w for w in good.warnings)


async def test_a_cli_that_prints_a_table_instead_of_json_is_a_clear_error_not_garbage():
    server = ScenarioServer()
    fake = FakeCli(ACLI_MAP, server, text_output=["jira.issue.get"])
    backend = AcliBackend(fake.runner())
    with pytest.raises(CliExecutionError) as exc:
        await backend.implementation_for(JiraIssueReader).get_issue("PROJ-1")
    assert "non-JSON" in str(exc.value) and "machine-readable" in exc.value.hint


# ---------------------------------------------------------------------------------------------------------------- end to end through the fake binary
async def test_issue_round_trip_through_a_fake_acli_keeps_long_text_out_of_argv():
    backend, fake, server = acli()
    w = backend.implementation_for(JiraIssueWriter)
    created = await w.create_issue(IssueDraft(project="PROJ", issue_type="Task", summary="Via CLI", description="secret text"))
    assert created.key.startswith("PROJ-")
    create_call = next(c for c in fake.calls if c[:3] == ("jira", "workitem", "create") and c[-1] != "--help")
    assert create_call[:9] == ("jira", "workitem", "create", "--project", "PROJ", "--type", "Task", "--summary", "Via CLI")
    assert "--description-file" in create_call and not any("secret text" in a for a in create_call)     # the description never appears in `ps`
    body = next(r for r in server.requests if r["method"] == "POST" and r["path"].endswith("/issue"))
    assert "secret text" in json.dumps(body.get("json"))
    await w.edit_issue("PROJ-1", IssueUpdate(summary="Changed"))
    assert any(r["method"] == "PUT" and r["path"].endswith("/issue/PROJ-1") for r in server.requests)


async def test_a_body_the_acli_flags_cannot_express_is_refused_so_the_router_falls_back():
    backend, _, _ = acli()
    with pytest.raises(UnsupportedOperationError):
        await backend.implementation_for(JiraIssueWriter).create_issue(IssueDraft(project="PROJ", issue_type="Task", summary="x",
                                                                                  fields={"customfield_10010": 3}))


@pytest.mark.parametrize("backend_class", [AcliBackend, TwgBackend, JiraCliBackend])
async def test_declared_operations_all_have_commands_after_composition(backend_class):
    backend = backend_class(FakeCli(backend_class.cmap, ScenarioServer()).runner())
    assert backend.supported_operations() <= backend.transport.operations | set(backend.COMPOSED)
    assert backend.supported_operations()


def test_a_board_location_printed_as_text_by_acli_still_yields_the_project_key():
    from atlian_assiant.backends.rest_cloud import normalize as n

    assert n.board({"id": 1, "name": "KAN board", "type": "simple", "location": "Gravity (KAN)"}).project_key == "KAN"
    assert n.board({"id": 1, "name": "b", "location": {"projectKey": "PROJ"}}).project_key == "PROJ"
