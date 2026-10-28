"""Forge companion app generator, ForgeCliRunner and App REST client (design 14.6, EV sections 8-17)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.backends.forge import (
    ForgeAppRestClient,
    ForgeAppSpec,
    ForgeCliRunner,
    TriggerFilter,
    generate,
    manifest,
    required_scopes,
    select_events,
)
from atlian_assiant.backends.forge.cli import CliResult
from atlian_assiant.backends.forge.generator import MAX_PUSH_BYTES, MAX_PUSH_EVENTS
from atlian_assiant.core.errors import (
    BackendUnavailableError,
    CliExecutionError,
    InvalidRequestError,
    UnsupportedDeliveryPathError,
)
from atlian_assiant.testing.server import ScenarioServer

pytestmark = [pytest.mark.layer_13]
ROOT = Path(__file__).parents[2]


def parsed(spec: ForgeAppSpec) -> dict:
    return yaml.safe_load(manifest(spec))


def test_committed_template_is_exactly_what_the_generator_produces():
    for rel, text in generate(ForgeAppSpec(app_name="event-inbox")).items():
        assert (ROOT / "forge_templates/event-inbox" / rel).read_text() == text, rel


def test_inbox_manifest_has_triggers_queue_consumer_inbox_writer_purge_and_rest_handler():
    m = parsed(ForgeAppSpec())
    triggers = {t["key"]: t for t in m["modules"]["trigger"]}
    assert set(triggers) == {"jira-events", "confluence-events"} and "avi:jira:updated:issue" in triggers["jira-events"]["events"]
    assert "avi:confluence:moved:page" in triggers["confluence-events"]["events"]
    assert m["modules"]["consumer"] == [{"key": "inbox-writer", "queue": "product-events", "function": "write-inbox"}]
    funcs = {f["key"]: f for f in m["modules"]["function"]}
    assert funcs["write-inbox"]["timeoutSeconds"] == 600 and {"purge-inbox", "app-rest"} <= set(funcs) and m["modules"]["scheduledTrigger"][0]["function"] == "purge-inbox"


def test_all_four_filter_options_are_exposed():
    m = parsed(ForgeAppSpec(products=("jira",), filter=TriggerFilter(ignore_self=False, expression='event.issue.fields.project.key == "PROJ"',
                                                                     app_is_licensed=True, on_error="IGNORE")))
    f = m["modules"]["trigger"][0]["filter"]
    assert f == {"ignoreSelf": False, "onError": "IGNORE", "appIsLicensed": True, "expression": 'event.issue.fields.project.key == "PROJ"'}
    assert parsed(ForgeAppSpec())["modules"]["trigger"][0]["filter"]["ignoreSelf"] is True          # self-triggered loops are off by default


def test_scopes_are_the_minimum_for_the_selected_events():
    assert required_scopes(ForgeAppSpec(products=("jira",), mode="native"), select_events(ForgeAppSpec(products=("jira",), mode="native"))) == ["read:jira-work"]
    inbox = ForgeAppSpec(products=("confluence",))
    assert required_scopes(inbox, select_events(inbox)) == ["read:confluence-content.all", "storage:app"]
    both = parsed(ForgeAppSpec())["permissions"]["scopes"]
    assert both == ["read:confluence-content.all", "read:jira-work", "storage:app"]
    assert "storage:app" not in parsed(ForgeAppSpec(mode="native"))["permissions"]["scopes"]


def test_event_selection_narrows_validates_and_covers_every_product():
    spec = ForgeAppSpec(products=("jira",), events=("issue:updated", "comment:created"))
    assert select_events(spec) == {"jira": ["avi:jira:updated:issue", "avi:jira:created:comment"]}
    everything = select_events(ForgeAppSpec(products=("jira", "jira-software", "jira-service-management", "confluence"), events="all"))
    assert "avi:jira-software:started:sprint" in everything["jira-software"] and "avi:jira-service-management:created:request" in everything["jira-service-management"]
    assert {"avi:confluence:created:whiteboard", "avi:confluence:added:label", "avi:confluence:performed:search", "avi:confluence:created:folder"} <= set(everything["confluence"])
    with pytest.raises(InvalidRequestError, match="not available"):
        select_events(ForgeAppSpec(products=("jira",), events=("page:created",)))
    with pytest.raises(InvalidRequestError, match="unknown products"):
        ForgeAppSpec(products=("bitbucket",)).validate()


def test_async_event_limits_are_enforced_in_the_generated_batcher_and_the_manifest():
    files = generate(ForgeAppSpec())
    assert f"MAX_EVENTS = {MAX_PUSH_EVENTS}" in files["src/events.js"] and f"MAX_BYTES = {MAX_PUSH_BYTES}" in files["src/events.js"] and (MAX_PUSH_EVENTS, MAX_PUSH_BYTES) == (50, 204800)
    with pytest.raises(InvalidRequestError, match="900"):
        ForgeAppSpec(consumer_timeout_seconds=901).validate()
    assert parsed(ForgeAppSpec(consumer_timeout_seconds=900))["modules"]["function"][1]["timeoutSeconds"] == 900


def test_storage_inbox_uses_ordered_keys_the_documented_record_shape_ttl_purge_and_cascade_marker():
    inbox = generate(ForgeAppSpec(retention_hours=24))["src/inbox.js"]
    assert "evt#${pad(s.receivedAt)}#${s.id}" in inbox and "TTL_MS = 24 * 3600" in inbox and "processed: false" in inbox
    assert all(k in inbox for k in ("source:", "eventType:", "entity:", "receivedAt:")) and "del#" in inbox and "storage.delete" in inbox


def test_app_rest_endpoints_cover_events_ack_health_stats_and_subscriptions():
    rest = generate(ForgeAppSpec())["src/rest.js"]
    for route in ("GET /events", "POST /events/ack", "GET /health", "GET /stats", "/subscriptions", "/events/(.+)/ack"):
        assert route.replace("/events/(.+)/ack", "events") in rest
    assert "VER-06" in rest and "VER-07" in rest and "100 per installation" in rest


def test_native_and_remote_modes_have_no_storage_and_remote_needs_https():
    native = generate(ForgeAppSpec(mode="native", products=("jira",)))
    assert "src/inbox.js" not in native and "consumer" not in parsed(ForgeAppSpec(mode="native"))["modules"] and "nothing leaves Atlassian" in native["src/events.js"]
    remote = ForgeAppSpec(mode="remote", remote_endpoint="https://py.example.com")
    m = parsed(remote)
    assert m["remotes"][0]["baseUrl"] == "https://py.example.com" and "invokeRemote" in generate(remote)["src/events.js"]
    for bad in (None, "http://py.example.com"):
        with pytest.raises(InvalidRequestError, match="https") as exc:
            ForgeAppSpec(mode="remote", remote_endpoint=bad).validate()
    assert "DM-07" in exc.value.hint                                                               # the no-ingress alternative is named


def test_realtime_panel_is_scoped_by_default_and_global_channels_are_refused():
    files = generate(ForgeAppSpec(realtime_panel="issue"))
    assert "changes-${contextId}" in files["src/panel.js"] and "VER-23" in files["src/panel.js"] and "jira:issuePanel" in files["manifest.yml"]
    assert "confluence:contentBylineItem" in generate(ForgeAppSpec(realtime_panel="page"))["manifest.yml"]
    with pytest.raises(UnsupportedDeliveryPathError, match="authorization design"):
        ForgeAppSpec(realtime_panel="issue", realtime_global=True).validate()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_generated_javascript_is_syntactically_valid_for_every_mode(tmp_path):
    for spec in (ForgeAppSpec(realtime_panel="issue"), ForgeAppSpec(mode="native"), ForgeAppSpec(mode="remote", remote_endpoint="https://py.example.com")):
        for rel, text in generate(spec).items():
            if rel.endswith(".js"):
                f = tmp_path / rel.replace("/", "_").replace(".js", ".mjs")
                f.write_text(text)
                assert subprocess.run(["node", "--check", str(f)], capture_output=True).returncode == 0, f"{spec.mode}:{rel}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_generated_batcher_never_exceeds_the_push_limits(tmp_path):
    events = generate(ForgeAppSpec())["src/events.js"].replace("import { Queue } from '@forge/events';", "class Queue { constructor() {} async push() {} }")
    f = tmp_path / "events.mjs"
    f.write_text(events + "\nconst big = Array.from({length: 130}, (_, i) => ({id: 'e' + i, pad: 'x'.repeat(4000)}));\n"
                 "const out = batches(big);\nconsole.log(JSON.stringify(out.map(b => [b.length, JSON.stringify(b).length])));\n")
    result = subprocess.run(["node", str(f)], capture_output=True, text=True, check=True)
    sizes = json.loads(result.stdout)
    assert sum(n for n, _ in sizes) == 130 and all(n <= 50 and size <= 204800 + 200 for n, size in sizes) and len(sizes) >= 3


# ---------------------------------------------------------------- ForgeCliRunner
class Recorder:
    def __init__(self, result=CliResult(0, "ok")):
        self.calls, self.result = [], result

    def __call__(self, argv, cwd):
        self.calls.append((list(argv), cwd))
        return self.result


def test_cli_runner_wraps_register_deploy_install_logs_and_variables(tmp_path):
    rec = Recorder()
    cli = ForgeCliRunner(runner=rec, which=lambda b: "/usr/bin/forge")
    cli.register(tmp_path, "my-app")
    cli.deploy(tmp_path, "staging")
    cli.install(tmp_path, site="example.atlassian.net", product="Jira", environment="production", upgrade=True)
    cli.logs(tmp_path, limit=10)
    cli.variables_set(tmp_path, "SECRET", "s3cr3t")
    assert [c[0] for c in rec.calls] == [
        ["forge", "register", "my-app"], ["forge", "deploy", "-e", "staging", "--non-interactive"],
        ["forge", "install", "--site", "example.atlassian.net", "--product", "Jira", "-e", "production", "--non-interactive", "--upgrade"],
        ["forge", "logs", "-e", "production", "--limit", "10"], ["forge", "variables", "set", "--encrypt", "-e", "production", "SECRET", "s3cr3t"]]
    assert all(c[1] == tmp_path for c in rec.calls)


def test_cli_runner_reports_a_missing_cli_and_failures_without_leaking_secrets(tmp_path):
    with pytest.raises(BackendUnavailableError, match="@forge/cli"):
        ForgeCliRunner(which=lambda b: None).deploy(tmp_path)
    failing = ForgeCliRunner(runner=Recorder(CliResult(1, "", "denied: s3cr3t is invalid")), which=lambda b: "/x")
    with pytest.raises(CliExecutionError) as exc:
        failing.variables_set(tmp_path, "SECRET", "s3cr3t")
    assert "s3cr3t" not in str(exc.value) and "***" in str(exc.value) and "forge login" in exc.value.hint


def test_cli_runner_never_uses_a_shell():
    import inspect

    from atlian_assiant.backends.forge import cli
    assert "shell=True" not in inspect.getsource(cli)


# ---------------------------------------------------------------- App REST client
@pytest.mark.anyio
async def test_app_rest_client_builds_the_documented_url_and_uses_3lo_style_surface():
    server = ScenarioServer()
    server.routes.append(("GET", re.compile(r"^/svc/jira/cid-1/apps/app-1_env-1/events$"),
                          lambda m, q, b: (200, {"events": [{"id": "x", "key": "evt#1#x"}], "nextCursor": "c2"})))
    server.routes.append(("POST", re.compile(r"^/svc/jira/cid-1/apps/app-1_env-1/events/ack$"), lambda m, q, b: (200, {"acked": len(b["ids"])})))
    cfg = AtlassianConfig(site="example.atlassian.net", email="a@b.c", api_token="tok-123456")
    transport = ClientFactory().backend(BackendKind.REST, cfg, http=server.client()).transport
    client = ForgeAppRestClient(transport, app_id="app-1", env_id="env-1", cloud_id="cid-1")
    page = await client.events(cursor="c1", limit=50)
    assert page["nextCursor"] == "c2" and server.requests[-1]["query"] == {"cursor": "c1", "limit": "50"}
    await client.ack(["evt#1#x"])
    assert server.requests[-1]["json"] == {"ids": ["evt#1#x"]}
    assert client.base == "https://api.atlassian.com/svc/jira/cid-1/apps/app-1_env-1"
    with pytest.raises(Exception, match="forge_app_id"):
        _ = ForgeAppRestClient(transport, app_id="", env_id="", cloud_id="").base
