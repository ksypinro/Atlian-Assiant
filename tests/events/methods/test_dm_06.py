"""DM-06: Forge triggers processed entirely inside Forge (ForgeAppProvisioner(native), ForgeLogVerifier)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from atlian_assiant.backends.forge import ForgeAppSpec, ForgeCliRunner
from atlian_assiant.core.errors import CliExecutionError, InvalidRequestError
from atlian_assiant.events.catalog import Ingress, MethodKind, method
from atlian_assiant.events.provision.base import EventProvisioner
from atlian_assiant.events.provision.forge import ForgeAppProvisioner, ForgeLogVerifier
from atlian_assiant.events.verify import verify_dm_06
from atlian_assiant.policy import CallbackConfirmation, DenyAll

from .forgesim import FakeForgeCli

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)
LOG = ('2026-09-01 12:00:00 INFO {"kind": "atlian-assiant-event", "eventType": "avi:jira:updated:issue", "id": "c1"}\n'
       "noise line\n"
       '2026-09-01 12:01:00 INFO {"kind": "atlian-assiant-event", "eventType": "avi:confluence:updated:page", "id": "c2"}\n'
       "2026-09-01 12:02:00 INFO atlian-assiant-event {not json}\n")


def provisioner(tmp_path, cli=None, **kw) -> ForgeAppProvisioner:
    return ForgeAppProvisioner(ForgeAppSpec(mode="native", products=("jira",)), directory=tmp_path / "app", site="example.atlassian.net",
                               cli=ForgeCliRunner(runner=cli or FakeForgeCli(), which=lambda b: "/usr/bin/forge"), **kw)


def test_dm_06_catalog_entry():
    m = method("DM-06")
    assert m.kind is MethodKind.IN_ATLASSIAN and m.ingress is Ingress.NONE and m.consumer.endswith("ForgeLogVerifier")
    assert isinstance(ForgeAppProvisioner(ForgeAppSpec(mode="native"), directory=Path("x")), EventProvisioner)


async def test_dm_06_provision_scaffolds_then_registers_deploys_and_installs(tmp_path):
    cli = FakeForgeCli()
    prov = provisioner(tmp_path, cli)
    plan = await prov.plan()
    assert plan.method_id == "DM-06" and plan.outputs["mode"] == "forge_cli" and any("one-time `forge login`" in s.description for s in plan.steps)
    out = await prov.apply(plan, confirm=APPROVE)
    assert yaml.safe_load((tmp_path / "app/manifest.yml").read_text())["modules"]["trigger"][0]["events"][0].startswith("avi:jira:")
    assert not (tmp_path / "app/src/inbox.js").exists() and out["installed"] == "Jira"
    assert [c[1] for c in cli.calls] == ["register", "deploy", "install"] and "--site" in cli.calls[2]


async def test_dm_06_provision_declined_writes_nothing_and_runs_nothing(tmp_path):
    cli = FakeForgeCli()
    prov = provisioner(tmp_path, cli)
    with pytest.raises(Exception, match="declined"):
        await prov.apply(await prov.plan(), confirm=DenyAll())
    assert cli.calls == [] and not (tmp_path / "app").exists()


async def test_dm_06_provision_guided_mode_works_without_the_cli_and_needs_no_site(tmp_path):
    prov = ForgeAppProvisioner(ForgeAppSpec(mode="native"), directory=tmp_path / "app", cli=ForgeCliRunner(which=lambda b: None))
    plan = await prov.plan()
    assert plan.outputs["mode"] == "guided"
    out = await prov.apply(plan, confirm=APPROVE)
    assert "forge deploy" in out["instructions"] and (tmp_path / "app/manifest.yml").exists()


async def test_dm_06_provision_cli_failures_are_reported_with_the_cli_message(tmp_path):
    cli = FakeForgeCli()
    cli.fail_on = "deploy"
    prov = provisioner(tmp_path, cli)
    with pytest.raises(CliExecutionError, match="boom"):
        await prov.apply(await prov.plan(), confirm=APPROVE)
    with pytest.raises(InvalidRequestError, match="site"):
        p = ForgeAppProvisioner(ForgeAppSpec(mode="native"), directory=tmp_path / "x", cli=ForgeCliRunner(runner=FakeForgeCli(), which=lambda b: "/f"))
        await p.apply(await p.plan(), confirm=APPROVE)


async def test_dm_06_provision_warns_about_loops_and_trigger_latency(tmp_path):
    from atlian_assiant.backends.forge import TriggerFilter
    prov = ForgeAppProvisioner(ForgeAppSpec(mode="native", filter=TriggerFilter(ignore_self=False)), directory=tmp_path, cli=ForgeCliRunner(which=lambda b: None))
    warnings = (await prov.plan()).warnings
    assert any("loop risk" in w for w in warnings) and any("3 minutes" in w for w in warnings)


def test_dm_06_log_parser_extracts_structured_lines_and_ignores_noise(tmp_path):
    parsed = ForgeLogVerifier(ForgeCliRunner(which=lambda b: "/f"), tmp_path).parse(LOG)
    assert [p["id"] for p in parsed] == ["c1", "c2"]


async def test_dm_06_verifier_passes_when_the_handler_logged_the_expected_event(tmp_path):
    cli = ForgeCliRunner(runner=FakeForgeCli(LOG), which=lambda b: "/f")
    verifier = ForgeLogVerifier(cli, tmp_path)
    report = await verify_dm_06(verifier=verifier, event_type="avi:jira:updated:issue")
    assert report.ok and report.method_id == "DM-06" and "1 matching" in report.detail
    none = await verify_dm_06(verifier=verifier, event_type="avi:jira:created:issue")
    assert not none.ok and "3 minutes" in none.detail


async def test_dm_06_verifier_reports_cli_problems_instead_of_raising(tmp_path):
    missing = await ForgeLogVerifier(ForgeCliRunner(which=lambda b: None), tmp_path).verify()
    assert not missing.ok and "cannot read forge logs" in missing.detail
