import pytest

from atlassian_client.core.errors import NotFoundError
from atlassian_client.protocols import JsmReader, JsmWriter

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]


async def test_jsm_write_create_request_and_comments(harness):
    w, r = harness.impl(JsmWriter), harness.impl(JsmReader)
    req = await w.create_request("4", "r1", {"summary": "Printer broken", "description": "Jammed"})
    assert req.issue_key.startswith("OPS-") and req.summary == "Printer broken"
    assert (await r.get_request(req.issue_key)).summary == "Printer broken"
    public = await w.add_request_comment("OPS-1", "We are on it", public=True)
    internal = await w.add_request_comment("OPS-1", "Escalating", public=False)
    assert public.public is True and internal.public is False
    visible = [c.id for c in (await r.list_request_comments("OPS-1", public_only=True)).items]
    assert public.id in visible and internal.id not in visible


async def test_jsm_write_transition_approval_and_customers(harness):
    w, r = harness.impl(JsmWriter), harness.impl(JsmReader)
    await w.transition_request("OPS-1", "711", comment="Done")
    assert (await r.get_request("OPS-1")).status != "Open"
    await w.answer_approval("OPS-1", "a1", approve=True)
    await w.add_customers("4", ["acc-new"])
    assert "acc-new" in [u.account_id or u.display_name for u in (await r.list_customers("4")).items]


async def test_jsm_write_wire_shapes_and_unknown_transition(harness):
    if harness.server is None:
        pytest.skip("wire-level assertion")
    w = harness.impl(JsmWriter)
    with pytest.raises(NotFoundError) as exc:
        await w.transition_request("OPS-1", "Teleport")
    assert "Resolve this issue" in exc.value.hint
    await w.transition_request("OPS-1", "Resolve this issue", comment="ok")
    sent = harness.server.requests[-1]
    assert sent["json"] == {"id": "711", "additionalComment": {"body": "ok"}}
    await w.answer_approval("OPS-1", "a1", approve=False)
    assert harness.server.requests[-1]["json"] == {"decision": "decline"}
    await w.create_request("4", "r1", {"summary": "x"}, on_behalf_of="acc-bob")
    assert harness.server.requests[-1]["json"]["raiseOnBehalfOf"] == "acc-bob"
