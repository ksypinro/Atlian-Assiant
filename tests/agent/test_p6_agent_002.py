"""P6.AGENT.002: MCP server mode (`atlian-assiant mcp serve`) and the atlian-assiant CLI (design 16.7, 16.8)."""
import json

from atlian_assiant import AtlassianConfig, ClientFactory, cli
from atlian_assiant.capabilities import Access
from atlian_assiant.policy import AccessPolicy
from atlian_assiant.testing.server import ScenarioServer

from .conftest import build


def fake_client(args=None):
    return build()


def rest_client(args=None):
    cfg = AtlassianConfig(site="example.atlassian.net", email="a@b.c", api_token="tok-123456", cloud_id="cid-1")
    pol = AccessPolicy(allowed=frozenset({Access.READ, Access.SEARCH, Access.WRITE, Access.MANAGE}))
    from atlian_assiant.policy import CallbackConfirmation

    return ClientFactory().build_async(cfg, preset="shell", policy=pol, confirm=CallbackConfirmation(lambda plan: True), http=ScenarioServer().client())


def run(argv, capsys, factory=fake_client):
    code = cli.main(argv, client_factory=factory)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_describe_prints_the_manifest_as_json(capsys):
    code, out, _ = run(["describe"], capsys)
    assert code == 0 and json.loads(out)["preset"] == "agent"


def test_jira_issue_get_search_and_count_render_markdown_or_json(capsys):
    code, out, _ = run(["jira", "issue", "get", "PROJ-1"], capsys)
    assert code == 0 and "Login fails" in out
    code, out, _ = run(["--json", "jira", "issue", "get", "PROJ-1", "--fields", "summary,status"], capsys)
    assert code == 0 and json.loads(out)["key"] == "PROJ-1"
    code, out, _ = run(["jira", "issue", "search", "project = PROJ", "--limit", "2"], capsys)
    assert code == 0 and "next_cursor" in out
    code, out, _ = run(["--json", "jira", "issue", "count", "project = PROJ"], capsys)
    assert json.loads(out) == {"count": 3}


def test_confluence_search_and_page(capsys):
    assert "Runbook" in run(["confluence", "search", 'space = "ENG"'], capsys)[1]
    assert "Restart **nginx**" in run(["confluence", "page", "123"], capsys)[1]


def test_atlassian_errors_exit_1_with_the_hint_on_stderr_and_bad_json_exits_2(capsys):
    code, out, err = run(["jira", "issue", "get", "PROJ-404"], capsys)
    assert code == 1 and out == "" and err.startswith("error:") and "hint:" in err and "permission" in err.lower()
    code, _, err = run(["tools", "invoke", "jira_get_issue", "--args", "{not json"], capsys)
    assert code == 2 and "valid JSON" in err


def test_events_methods_and_recommend(capsys):
    code, out, _ = run(["events", "methods", "--implemented", "--kind", "machine_push"], capsys)
    assert code == 0 and "| DM-01 |" in out
    code, out, _ = run(["--json", "events", "recommend", "--products", "jira,confluence", "--no-ingress", "--aws"], capsys)
    assert code == 0 and "DM-36" in out


def test_webhooks_ensure_is_idempotent(capsys):
    client = rest_client()
    args = ["--yes", "webhooks", "jira", "ensure", "--url", "https://relay.example.com/jira", "--events", "jira:issue_updated"]
    code = cli.main(args, client_factory=lambda a: client)
    first = capsys.readouterr()
    assert code == 0, first.err
    code = cli.main(["--yes", "webhooks", "jira", "ensure", "--url", "https://relay.example.com/jira", "--events", "jira:issue_updated,jira:issue_created"], client_factory=lambda a: client)
    capsys.readouterr()
    assert code == 0
    code = cli.main(["--json", "webhooks", "jira", "list"], client_factory=lambda a: client)
    hooks = json.loads(capsys.readouterr().out)
    assert code == 0 and len(hooks) == 1 and hooks[0]["events"] == ["jira:issue_updated", "jira:issue_created"]


def test_forge_scaffold_writes_a_deployable_app_without_credentials(capsys, tmp_path, monkeypatch):
    monkeypatch.delenv("ATLASSIAN_SITE", raising=False)
    code = cli.main(["--json", "forge", "scaffold", "--products", "jira,confluence", "--out", str(tmp_path / "app")])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and "manifest.yml" in out["written"] and "avi:jira:created:issue" in (tmp_path / "app" / "manifest.yml").read_text()
    assert "forge deploy" in out["next"]


def test_tools_export_list_and_invoke(capsys):
    code, out, _ = run(["tools", "export", "--format", "anthropic"], capsys)
    exported = json.loads(out)
    assert code == 0 and len(exported) == 39 and {"name", "description", "input_schema"} <= set(exported[0])
    assert "inputSchema" in json.loads(run(["tools", "export", "--format", "mcp"], capsys)[1])[0]
    assert "jira_get_issue" in run(["tools", "list"], capsys)[1]
    code, out, _ = run(["tools", "invoke", "jira_get_issue", "--args", '{"key": "PROJ-1"}'], capsys)
    assert code == 0 and "untrusted DATA" in out
    code, out, _ = run(["tools", "invoke", "jira_get_issue", "--args", "{}"], capsys)
    assert code == 1 and "key" in out


def test_operations_discover_and_call(capsys):
    code, out, _ = run(["--json", "operations", "discover", "issue watchers", "--limit", "3"], capsys)
    assert code == 0 and any(h["id"] == "api.jira.getIssueWatchers" for h in json.loads(out))
    code, out, _ = run(["--json", "operations", "call", "jira.issue.get", "--args", '{"key": "PROJ-1"}'], capsys)
    assert code == 0 and json.loads(out)["key"] == "PROJ-1"


def test_mcp_serve_over_http_refuses_a_public_bind_without_a_token(capsys, monkeypatch):
    monkeypatch.delenv("ATLASSIAN_MCP_TOKEN", raising=False)
    code, _, err = run(["mcp", "serve", "--transport", "http", "--host", "0.0.0.0"], capsys)
    assert code == 1 and "bearer token" in err


def test_the_default_client_honours_profile_free_environment_access_and_preset(monkeypatch, capsys):
    monkeypatch.setenv("ATLASSIAN_SITE", "example.atlassian.net")
    monkeypatch.setenv("ATLASSIAN_EMAIL", "a@b.c")
    monkeypatch.setenv("ATLASSIAN_API_TOKEN", "tok-123456")
    args = cli.build_parser().parse_args(["--access", "read,search,write", "--preset", "agent", "--yes", "describe"])
    client = cli.default_client(args)
    assert client.preset.value == "agent" and client.policy.allowed == {Access.READ, Access.SEARCH, Access.WRITE}
    default = cli.default_client(cli.build_parser().parse_args(["describe"]))
    assert default.preset.value == "shell" and Access.WRITE not in default.policy.allowed


def test_confirmation_without_a_terminal_is_refused_not_assumed(monkeypatch, capsys):
    args = cli.build_parser().parse_args(["describe"])
    ask = cli._confirmer(args, lambda fn: fn)
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": lambda self: False})())
    assert ask(type("P", (), {"summary": "create issue"})()) is False and "stdin is not a terminal" in capsys.readouterr().err
    yes = cli._confirmer(cli.build_parser().parse_args(["--yes", "describe"]), lambda fn: fn)
    assert yes(object()) is True
