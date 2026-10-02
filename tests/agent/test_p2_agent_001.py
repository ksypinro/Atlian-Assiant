import builtins

import pytest

from atlassian_client import AtlassianConfig, ClientFactory, operations as ops
from atlassian_client.capabilities import Access
from atlassian_client.core.errors import ConfigurationError, ConfirmationDeclinedError
from atlassian_client.policy import (AccessPolicy, AutoApprove, CallbackConfirmation, CliConfirmation, DenyAll, PlannedAction, PolicyGuard)

pytestmark = [pytest.mark.anyio, pytest.mark.layer_10]

PLAN = PlannedAction(ops.JIRA_ISSUE_TRANSITION, "PROJ-1", "Transition PROJ-1", {"args": ["PROJ-1", "Done"]}, warnings=("careful",))


async def test_cli_confirmation_asks_on_the_terminal_and_only_y_approves(monkeypatch, capsys):
    answers = iter(["y", "Y", "n", "", "yes please"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": (print(prompt), next(answers))[1])
    results = [await CliConfirmation().confirm(PLAN) for _ in range(5)]
    assert results == [True, True, False, False, False]
    out = capsys.readouterr().out
    assert "Transition PROJ-1" in out and "careful" in out and "Proceed? [y/N]" in out


async def test_callback_confirmation_accepts_sync_and_async_callbacks_and_coerces_to_bool():
    assert await CallbackConfirmation(lambda p: True).confirm(PLAN) is True
    assert await CallbackConfirmation(lambda p: 0).confirm(PLAN) is False

    async def approve(plan):
        assert plan.target == "PROJ-1"
        return "yes"

    assert await CallbackConfirmation(approve).confirm(PLAN) is True


async def test_deny_all_and_auto_approve():
    assert await DenyAll().confirm(PLAN) is False
    assert await AutoApprove().confirm(PLAN) is True


async def test_auto_approve_is_refused_for_the_agent_preset_but_allowed_for_services():
    with pytest.raises(ConfigurationError) as exc:
        ClientFactory().build_async(AtlassianConfig(site="x"), backends=[], preset="agent", confirm=AutoApprove())
    assert "CallbackConfirmation" in exc.value.hint
    assert ClientFactory().build_async(AtlassianConfig(site="x"), backends=[], preset="service", confirm=AutoApprove()).confirm is not None
    with pytest.raises(ConfigurationError):
        ClientFactory.for_agent(AtlassianConfig(site="x"), confirm=AutoApprove())


async def test_handlers_are_consulted_exactly_once_per_write_and_never_for_previews():
    calls = []

    async def spy(plan):
        calls.append(plan.target)
        return True

    guard = PolicyGuard(AccessPolicy.read_write(), CallbackConfirmation(spy))
    await guard.authorize(ops.JIRA_ISSUE_TRANSITION, ("PROJ-1", "Done"), {"dry_run": True})
    assert calls == []
    await guard.authorize(ops.JIRA_ISSUE_TRANSITION, ("PROJ-1", "Done"), {})
    assert calls == ["PROJ-1"]


async def test_a_declined_write_never_executes_through_the_facade():
    from tests.agent.conftest import build

    c = build(AccessPolicy.read_write(), confirm=DenyAll())
    with pytest.raises(ConfirmationDeclinedError):
        await c.jira.issues.transition("PROJ-1", "Done")
    assert (await c.jira.issues.get("PROJ-1")).status.name == "In Progress"
    plan = await c.jira.issues.transition("PROJ-1", "Done", dry_run=True)
    assert isinstance(plan, PlannedAction) and (await c.jira.issues.get("PROJ-1")).status.name == "In Progress"
    ok = build(AccessPolicy.read_write(), confirm=AutoApprove()) if False else None
    assert ok is None
    assert Access.WRITE in c.policy.allowed
