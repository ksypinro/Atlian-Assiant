"""DM-18: Jira Automation -> AWS SSM (SsmProvisioner, SsmDocumentAdapter, ssm_handler, verify_dm_18)."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from atlian_assiant.events import ssm_handler
from atlian_assiant.events.catalog import Ingress, method
from atlian_assiant.events.model import ChangeAction, EntityType, EventSourceKind
from atlian_assiant.events.provision.aws import SsmProvisioner, ssm_document, ssm_send_command_policy
from atlian_assiant.events.provision.base import EventProvisioner
from atlian_assiant.events.sources.ssm import SsmDocumentAdapter, load_handler
from atlian_assiant.events.verify import verify_dm_18
from atlian_assiant.policy import CallbackConfirmation

from .awssim import aws
from .sitesim import envelope

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)
HANDLER = "tests.events.methods.handlers_for_tests:record"


def prov(clients=None, **kw) -> SsmProvisioner:
    return SsmProvisioner(clients, handler=HANDLER, region="eu-west-1", account_id="123456789012", external_id=kw.pop("external_id", None), **kw)


def test_dm_18_catalog_entry():
    m = method("DM-18")
    assert m.ingress is Ingress.NONE and m.consumer.endswith("SsmDocumentAdapter") and isinstance(prov(), EventProvisioner)


def test_dm_18_document_runs_the_python_handler_with_the_payload_parameter():
    doc = ssm_document("Doc", HANDLER)
    cmd = doc["mainSteps"][0]["inputs"]["runCommand"][0]
    assert doc["schemaVersion"] == "2.2" and doc["parameters"]["payload"]["type"] == "String"
    assert cmd == f"python -m atlian_assiant.events.ssm_handler --handler {HANDLER} --payload '{{{{ payload }}}}'"


def test_dm_18_iam_policy_is_limited_to_one_document_and_tagged_nodes():
    policy = json.loads(ssm_send_command_policy("eu-west-1", "123456789012", "Doc", "atlassian-handler", "true"))
    resources = [s["Resource"] for s in policy["Statement"]]
    assert resources[0] == "arn:aws:ssm:eu-west-1:123456789012:document/Doc"
    assert all("Condition" in s for s in policy["Statement"][1:]) and all(s["Action"] == "ssm:SendCommand" for s in policy["Statement"])
    assert "*" not in resources[0]


async def test_dm_18_provision_creates_the_document_and_for_on_prem_nodes_a_hybrid_activation():
    clients = aws()
    p = prov(clients, on_prem=True, external_id="ext-1", automation_principal="arn:aws:iam::1:root")
    plan = await p.plan()
    assert plan.method_id == "DM-18" and any("hybrid activation" in s.description for s in plan.steps) and any("VER-16" in w for w in plan.warnings)
    out = await p.apply(plan, confirm=APPROVE)
    assert clients.ssm.documents["AtlassianClientHandler"]["schemaVersion"] == "2.2" and out["activation_id"] == "act-1" and "SECRETCODE" not in json.dumps(out)
    assert out["tag"] == "atlassian-handler=true" and clients.iam.policies[("AtlassianClientHandler-automation", "send-command")]
    await p.teardown(confirm=APPROVE)
    assert clients.ssm.documents == {}


async def test_dm_18_provision_without_on_prem_has_no_activation_and_guided_mode_prints_the_artifacts():
    clients = aws()
    out = await prov(clients).apply(await prov(clients).plan(), confirm=APPROVE)
    assert "activation_id" not in out and "role_arn" not in out
    guided = prov(None)
    plan = await guided.plan()
    assert plan.outputs["mode"] == "guided" and "mainSteps" in (await guided.apply(plan, confirm=APPROVE))["document_json"]


async def adapter_run(payload: str, **kw) -> tuple[int, list]:
    seen: list = []

    async def handler(ev):
        if kw.get("fail"):
            raise RuntimeError("handler broke")
        seen.append(ev)
    import io
    err = io.StringIO()
    code = await SsmDocumentAdapter(handler, stderr=err).run(payload)
    return code, seen if not err.getvalue() else [err.getvalue()]


async def test_dm_18_consumer_normalizes_the_payload_and_calls_the_handler_with_exit_code_zero():
    code, seen = await adapter_run(json.dumps(envelope("jira", "PROJ-8", "issue_created")))
    ev = seen[0]
    assert code == 0 and (ev.entity_type, ev.action, ev.entity_key, ev.source) == (EntityType.ISSUE, ChangeAction.CREATED, "PROJ-8", EventSourceKind.AWS_SSM)
    assert ev.delivery.method_id == "DM-18"


async def test_dm_18_consumer_exit_code_is_one_on_bad_payloads_and_handler_errors():
    assert (await adapter_run("not json"))[0] == 1
    assert (await adapter_run("[1, 2]"))[0] == 1
    code, seen = await adapter_run(json.dumps(envelope("jira", "PROJ-9", "issue_created")), fail=True)
    assert code == 1 and "handler broke" in seen[0]


async def test_dm_18_consumer_enrichment_skips_stale_versions_with_exit_code_zero():
    from atlian_assiant.events.enrich import CanonicalEnricher
    from atlian_assiant.events.stores import MemoryVersionStore
    calls = []

    async def stale(ev):
        calls.append(ev)

    class Enr(CanonicalEnricher):
        async def enrich(self, event):
            return None
    code = await SsmDocumentAdapter(stale, enricher=Enr(lambda k: None, lambda *a: None, MemoryVersionStore())).run(json.dumps(envelope("jira", "PROJ-1", "issue_created")))  # type: ignore
    assert code == 0 and calls == []


def test_dm_18_handler_loader_validates_the_path_and_the_cli_returns_exit_codes(capsys):
    assert callable(load_handler(HANDLER))
    with pytest.raises(ValueError):
        load_handler("nocolon")
    assert ssm_handler.main(["--handler", "no.such.module:fn", "--payload", "{}"]) == 2
    assert "cannot load handler" in capsys.readouterr().err
    assert ssm_handler.main(["--handler", HANDLER, "--payload", json.dumps(envelope("jira", "PROJ-1", "issue_created"))]) == 0
    assert ssm_handler.main(["--handler", HANDLER, "--payload", "oops"]) == 1


def test_dm_18_module_runs_as_a_command_the_way_the_ssm_document_invokes_it():
    r = subprocess.run([sys.executable, "-m", "atlian_assiant.events.ssm_handler", "--handler", HANDLER, "--payload",
                        json.dumps(envelope("jira", "PROJ-1", "issue_created"))], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


async def test_dm_18_verify_reads_get_command_invocation():
    clients = aws()
    clients.ssm.invocations[("c1", "i-1")] = {"Status": "Success"}
    clients.ssm.invocations[("c2", "i-1")] = {"Status": "Failed", "StandardErrorContent": "Traceback..."}
    assert (await verify_dm_18(ssm=clients.ssm, command_id="c1", instance_id="i-1")).ok
    bad = await verify_dm_18(ssm=clients.ssm, command_id="c2", instance_id="i-1")
    assert not bad.ok and "Failed" in bad.detail and "Traceback" in bad.detail
    assert "failed" in (await verify_dm_18(ssm=clients.ssm, command_id="nope", instance_id="i-1")).detail.lower()
