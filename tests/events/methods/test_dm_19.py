"""DM-19: Jira Edge Connector (JecProvisioner, JecScriptAdapter, jec module, verify_dm_19)."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from atlian_assiant.core.errors import InvalidRequestError
from atlian_assiant.events import jec
from atlian_assiant.events.automation import AutomationAction
from atlian_assiant.events.catalog import Ingress, method
from atlian_assiant.events.model import ChangeAction, EntityType, EventSourceKind
from atlian_assiant.events.provision.base import EventProvisioner
from atlian_assiant.events.provision.jec import JecProvisioner, jec_config_yaml, systemd_unit
from atlian_assiant.events.sources.jec import DefaultJecPayloadParser, JecScriptAdapter, run_jec_handler
from atlian_assiant.policy import CallbackConfirmation

from .sitesim import envelope

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)
HANDLER = "tests.events.methods.handlers_for_tests:record"
ENV = json.dumps(envelope("jira", "PROJ-5", "issue_created"))


def prov(tmp_path, **kw) -> JecProvisioner:
    return JecProvisioner(directory=tmp_path / "jec", base_url="https://api.atlassian.com", handler=HANDLER, entitlement_confirmed=kw.pop("entitlement_confirmed", True),
                          run=kw.pop("run", lambda argv: "jec version 2.3.1"), **kw)


def test_dm_19_catalog_entry(tmp_path):
    m = method("DM-19")
    assert m.ingress is Ingress.NONE and m.entitlements == ("JSM Premium or Enterprise",) and isinstance(prov(tmp_path), EventProvisioner)


def test_dm_19_config_references_the_secret_and_maps_the_action_to_the_python_handler():
    cfg = jec_config_yaml(app_name="a", base_url="https://api.atlassian.com", api_key_ref="JEC_API_KEY", handler=HANDLER)
    assert "apiKey: ${JEC_API_KEY}" in cfg and f'"--handler", "{HANDLER}"' in cfg and "pollingWaitIntervalInMillis" in cfg and "visibilityTimeoutInSeconds" in cfg and "maxNumberOfMessages" in cfg
    assert "secret" not in cfg.lower().replace("secret reference", "") or "api key" in cfg.lower()
    unit = systemd_unit("/usr/local/bin/jec", __import__("pathlib").Path("/etc/jec/jec-config.yaml"), "JEC_API_KEY")
    assert "NoNewPrivileges=true" in unit and "User=jec" in unit and "EnvironmentFile=/etc/jec/JEC_API_KEY.env" in unit


async def test_dm_19_provision_writes_config_and_unit_checks_the_binary_and_emits_the_template(tmp_path):
    p = prov(tmp_path)
    plan = await p.plan()
    assert plan.method_id == "DM-19" and any("Premium or Enterprise" in w for w in plan.warnings) and any("VER-02" in w for w in plan.warnings)
    out = await p.apply(plan, confirm=APPROVE)
    assert (tmp_path / "jec/jec-config.yaml").exists() and (tmp_path / "jec/jec.service").exists() and out["binary_version"] == "jec version 2.3.1"
    assert p.template().action is AutomationAction.JEC and out["automation_template"] == "Run script using Jira Edge Connector"
    await p.teardown(confirm=APPROVE)
    assert not (tmp_path / "jec/jec-config.yaml").exists()


async def test_dm_19_provision_refuses_without_the_entitlement_confirmation_or_an_old_binary(tmp_path):
    unentitled = prov(tmp_path, entitlement_confirmed=False)
    plan = await unentitled.plan()
    assert any("must confirm" in w for w in plan.warnings)
    with pytest.raises(InvalidRequestError, match="entitlement"):
        await unentitled.apply(plan, confirm=APPROVE)
    old = prov(tmp_path, run=lambda argv: "jec 0.9.1")
    with pytest.raises(InvalidRequestError, match="older"):
        await old.apply(await old.plan(), confirm=APPROVE)
    assert not (tmp_path / "jec").exists()
    with pytest.raises(InvalidRequestError, match="cannot check"):
        JecProvisioner(directory=tmp_path, base_url="x", handler=HANDLER).check_binary()


def test_dm_19_default_parser_accepts_payload_flags_key_value_pairs_and_stdin():
    p = DefaultJecPayloadParser()
    assert p.parse(["--payload", ENV], "")["entity_key"] == "PROJ-5"
    assert p.parse(["-payload", ENV], "")["trigger"] == "issue_created"
    assert p.parse(["--entity_key", "PROJ-6", "-trigger=issue_created"], "") == {"entity_key": "PROJ-6", "trigger": "issue_created"}
    assert p.parse([], ENV)["entity_key"] == "PROJ-5"
    assert p.parse(["--entity_key", "FROM-ARG"], ENV)["entity_key"] == "FROM-ARG"          # arguments win over stdin
    assert p.parse([], "not json") == {}


async def test_dm_19_adapter_normalizes_runs_the_handler_and_returns_exit_codes():
    seen = []

    async def handler(ev):
        seen.append(ev)
    code = await JecScriptAdapter(handler).run(["--payload", ENV])
    ev = seen[0]
    assert code == 0 and (ev.entity_type, ev.action, ev.entity_key, ev.source, ev.delivery.method_id) == (
        EntityType.ISSUE, ChangeAction.CREATED, "PROJ-5", EventSourceKind.JEC, "DM-19")

    async def broken(ev):
        raise RuntimeError("nope")
    import io
    err = io.StringIO()
    assert await JecScriptAdapter(broken, stderr=err).run(["--payload", ENV]) == 1 and "nope" in err.getvalue()
    assert await JecScriptAdapter(handler, stderr=io.StringIO()).run(["--payload", "{broken"]) == 1


async def test_dm_19_adapter_accepts_a_pluggable_parser_because_the_convention_is_unverified():
    class Custom:
        def parse(self, argv, stdin):
            return {"entity_type": "issue", "entity_key": argv[-1], "trigger": "issue_created", "atl_event_id": "x"}
    seen = []

    async def handler(ev):
        seen.append(ev.entity_key)
    assert await JecScriptAdapter(handler, parser=Custom()).run(["script", "PROJ-77"]) == 0 and seen == ["PROJ-77"]


def test_dm_19_run_jec_handler_is_the_documented_entry_point():
    seen = []

    async def handler(ev):
        seen.append(ev.entity_key)
    assert run_jec_handler(handler, ["--payload", ENV], stdin="") == 0 and seen == ["PROJ-5"]


def test_dm_19_module_runs_as_a_command_the_way_jec_invokes_it(capsys):
    assert jec.main(["--handler", "no.such:fn"]) == 2 and "cannot load handler" in capsys.readouterr().err
    assert jec.main(["--handler", HANDLER, "--payload", ENV]) == 0
    r = subprocess.run([sys.executable, "-m", "atlian_assiant.events.jec", "--handler", HANDLER, "--payload", ENV], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr


async def test_dm_19_end_to_end_verify_delivers_the_probe_through_the_jec_script():
    from datetime import timedelta

    from atlian_assiant.events.config import EventPlaneConfig
    from atlian_assiant.events.verify import EventInbox, verify_dm_19

    from .sitesim import SiteSim
    inbox = EventInbox()

    async def handler(ev):
        await inbox.push(ev)

    async def on_issue(key):
        await JecScriptAdapter(handler).run(["--payload", json.dumps(envelope("jira", key, "field_changed"))])
    report = await verify_dm_19(client=SiteSim(on_issue=on_issue), config=EventPlaneConfig(probe_issue="SBX-19"), inbox=inbox, timeout=timedelta(seconds=2))
    assert report.ok and report.method_id == "DM-19"
