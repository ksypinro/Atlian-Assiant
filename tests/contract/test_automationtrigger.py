import pytest

from atlian_assiant.core.errors import ConfigurationError
from atlian_assiant.protocols import AutomationTrigger

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]

URL = "https://automation.atlassian.com/pg/webhooks/abc-123"


async def test_automation_trigger_fires_the_rule_with_issues_and_data(harness):
    t = harness.impl(AutomationTrigger)
    await t.trigger_incoming_webhook(URL, secret="tok", issue_keys=["PROJ-1", "PROJ-2"], data={"reason": "nightly"})
    if harness.server is not None:
        call = harness.server.automation_calls[-1]
        assert call["path"] == "/pg/webhooks/abc-123" and call["body"] == {"reason": "nightly", "issues": ["PROJ-1", "PROJ-2"]}
    else:
        assert harness.backend.automation.triggered[-1]["issues"] == ["PROJ-1", "PROJ-2"]


async def test_automation_trigger_never_sends_api_credentials_to_the_automation_host(harness):
    if harness.server is None:
        pytest.skip("wire-level assertion")
    await harness.impl(AutomationTrigger).trigger_incoming_webhook(URL, secret="tok")
    sent = harness.server.requests[-1]
    assert "authorization" not in {k.lower() for k in sent["headers"]} and sent["headers"]["x-automation-webhook-token"] == "tok"


async def test_automation_trigger_refuses_foreign_hosts_and_plain_http(harness):
    if harness.server is None:
        pytest.skip("rest-only guard")
    t = harness.impl(AutomationTrigger)
    for bad in ("https://evil.example.com/pg/webhooks/abc", "http://automation.atlassian.com/pg/webhooks/abc", "https://example.atlassian.net/pg/webhooks/abc"):
        with pytest.raises(ConfigurationError):
            await t.trigger_incoming_webhook(bad, secret="tok")
    assert not any(r["path"].startswith("/pg/") for r in harness.server.requests)
