"""End-to-end verification (design section 14.3): a harmless change on a CONFIGURED sandbox target must come back as an event."""
from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any, Protocol

import anyio

from ..core.errors import AtlassianError
from .config import EventPlaneConfig
from .model import ChangeEvent
from .provision.base import VerificationReport

PROBE_PROPERTY = "atlian_assiant.probe"
PROBE_LABEL = "atlian-assiant-probe"
Matcher = Callable[[ChangeEvent], bool]


class EventInbox:
    """Collects events delivered by a consumer so a probe (or a test) can wait for a specific one."""

    def __init__(self) -> None:
        self.events: list[ChangeEvent] = []
        self._changed = anyio.Condition()

    async def push(self, event: ChangeEvent) -> None:
        async with self._changed:
            self.events.append(event)
            self._changed.notify_all()

    def attach(self, pipeline: Any) -> None:
        """Register a catch-all handler on an ``EventPipeline`` so every delivered event lands here."""
        pipeline.on()(self.push)

    async def wait_for(self, match: Matcher, timeout: float, *, start: int = 0) -> ChangeEvent | None:
        with anyio.move_on_after(timeout):
            async with self._changed:
                while True:
                    hit = next((e for e in self.events[start:] if match(e)), None)
                    if hit is not None:
                        return hit
                    await self._changed.wait()
        return None


class ProbeTrigger(Protocol):
    async def __call__(self, target: str, token: str) -> None: ...


class EndToEndProbe:
    """Mutates ONLY the configured sandbox target, waits for the matching event on the method's consumer and records latency.

    * the trigger receives the configured target and nothing else, so a probe cannot touch other objects;
    * with no target configured nothing is changed and the report says what to configure;
    * the mutation goes through the client, so it needs WRITE access and the user's confirmation like any other write.
    """

    def __init__(self, method_id: str, *, target: str | None, target_name: str, trigger: ProbeTrigger, inbox: EventInbox, match: Matcher | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.method_id, self.target, self.target_name = method_id, target, target_name
        self._trigger, self._inbox, self._match, self._clock = trigger, inbox, match, clock

    async def run(self, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
        if not self.target:
            return VerificationReport(self.method_id, False, None,
                                      f"{self.target_name} is not configured; set ATLASSIAN_EVENTS_{self.target_name.upper()} to a sandbox target. Nothing was changed.")
        token = uuid.uuid4().hex[:12]
        start_index = len(self._inbox.events)
        started = self._clock()
        try:
            await self._trigger(self.target, token)
        except AtlassianError as exc:
            return VerificationReport(self.method_id, False, None, f"probe could not change the sandbox target: {type(exc).__name__}: {exc.message}"
                                      + (f" ({exc.hint})" if exc.hint else ""))
        target = self.target
        match = self._match or (lambda e: target in (e.entity_key, e.entity_id))
        event = await self._inbox.wait_for(match, timeout.total_seconds(), start=start_index)
        if event is None:
            return VerificationReport(self.method_id, False, None, f"no event for {target} within {timeout.total_seconds():.0f}s; check the callback URL, "
                                      "the registration and the consumer's logs")
        return VerificationReport(self.method_id, True, self._clock() - started, f"received {event.native_type} for {target}")


# ---------------------------------------------------------------------------------------------------------------------
# Triggers built on the client (writes: need WRITE access + confirmation, enforced by the client's policy)
# ---------------------------------------------------------------------------------------------------------------------
def issue_property_trigger(client: Any) -> ProbeTrigger:
    async def trigger(target: str, token: str) -> None:
        await client.jira.issues.set_property(target, PROBE_PROPERTY, {"probe": token})
        await client.jira.issues.set_property(target, PROBE_PROPERTY, {"probe": None})        # cleared again: leaves nothing behind
    return trigger


def jira_label_trigger(client: Any) -> ProbeTrigger:
    from ..models.jira import IssueUpdate

    async def trigger(target: str, token: str) -> None:
        await client.jira.issues.edit(target, IssueUpdate(labels_add=[PROBE_LABEL]))
        await client.jira.issues.edit(target, IssueUpdate(labels_remove=[PROBE_LABEL]))
    return trigger


def confluence_label_trigger(client: Any) -> ProbeTrigger:
    async def trigger(target: str, token: str) -> None:
        await client.confluence.labels.add(target, [PROBE_LABEL])
        await client.confluence.labels.remove(target, PROBE_LABEL)
    return trigger


async def _run(method_id: str, *, target: str | None, target_name: str, trigger: ProbeTrigger, inbox: EventInbox, timeout: timedelta,
               match: Matcher | None = None) -> VerificationReport:
    return await EndToEndProbe(method_id, target=target, target_name=target_name, trigger=trigger, inbox=inbox, match=match).run(timeout)


async def verify_dm_01(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
    """DM-01 (admin-UI webhook): set and clear the probe issue property; the webhook must deliver the event."""
    return await _run("DM-01", target=config.probe_issue, target_name="probe_issue", trigger=issue_property_trigger(client), inbox=inbox, timeout=timeout)


async def verify_dm_02(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
    """DM-02 (admin webhook REST): as DM-01."""
    return await _run("DM-02", target=config.probe_issue, target_name="probe_issue", trigger=issue_property_trigger(client), inbox=inbox, timeout=timeout)


async def verify_dm_03(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
    """DM-03 (dynamic webhook REST): as DM-01."""
    return await _run("DM-03", target=config.probe_issue, target_name="probe_issue", trigger=issue_property_trigger(client), inbox=inbox, timeout=timeout)


async def verify_dm_05(*, client: Any, config: EventPlaneConfig | None = None, inbox: EventInbox | None = None,
                       timeout: timedelta = timedelta(minutes=1)) -> VerificationReport:
    """DM-05 (failed-webhook replay): the failed-delivery API must answer. Read-only, nothing is changed."""
    started = time.monotonic()
    try:
        failed = await client.webhooks.failed()
    except AtlassianError as exc:
        return VerificationReport("DM-05", False, None, f"failed-webhook API unavailable: {type(exc).__name__}: {exc.message}")
    return VerificationReport("DM-05", True, time.monotonic() - started, f"{len(failed)} failed delivery(ies) pending replay")


async def verify_dm_15(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
    """DM-15 (Jira Automation web request): toggle the probe label on the probe issue; the rule must post the envelope."""
    return await _run("DM-15", target=config.probe_issue, target_name="probe_issue", trigger=jira_label_trigger(client), inbox=inbox, timeout=timeout)


async def verify_dm_16(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
    """DM-16 (Confluence Automation web request): toggle the probe label on the probe page; the rule must post the envelope."""
    return await _run("DM-16", target=config.probe_page, target_name="probe_page", trigger=confluence_label_trigger(client), inbox=inbox, timeout=timeout)


async def verify_dm_36(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=10)) -> VerificationReport:
    """DM-36 (JQL polling): toggle the probe label (bumps `updated`); the poller must emit the issue."""
    return await _run("DM-36", target=config.probe_issue, target_name="probe_issue", trigger=jira_label_trigger(client), inbox=inbox, timeout=timeout)


async def verify_dm_37(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=10)) -> VerificationReport:
    """DM-37 (CQL polling): toggle the probe label on the probe page; the poller must emit the page."""
    return await _run("DM-37", target=config.probe_page, target_name="probe_page", trigger=confluence_label_trigger(client), inbox=inbox, timeout=timeout)


async def verify_dm_38(*, reconciler: Any, **_: Any) -> VerificationReport:
    """DM-38 (periodic reconciliation): one sweep must complete; reports how many entities were compared and how many deletes were found."""
    started = time.monotonic()
    try:
        result = await reconciler.sweep()
    except AtlassianError as exc:
        return VerificationReport("DM-38", False, None, f"reconciliation sweep failed: {type(exc).__name__}: {exc.message}")
    return VerificationReport("DM-38", True, time.monotonic() - started, f"compared {result.compared} entities, {result.deleted} missing, {result.changed} changed")



async def _audit_crosscheck(method_id: str, *, client: Any, inbox: EventInbox, timeout: timedelta) -> VerificationReport:
    """Audit methods use a cross-check instead of a probe: the newest /events item must also arrive on the stream or webhook (read-only)."""
    started = time.monotonic()
    try:
        page = await client.admin.audit.events(limit=1)
    except AtlassianError as exc:
        return VerificationReport(method_id, False, None, f"cannot read /events: {type(exc).__name__}: {exc.message}")
    if not page.items:
        return VerificationReport(method_id, False, None, "the audit log has no events yet to cross-check against")
    newest = page.items[0].id
    hit = await inbox.wait_for(lambda e: e.event_id == newest, timeout.total_seconds())
    if hit is None:
        return VerificationReport(method_id, False, None, f"newest /events item {newest} did not arrive within {timeout.total_seconds():.0f}s")
    return VerificationReport(method_id, True, time.monotonic() - started, f"newest audit event {newest} arrived via {hit.source.value}")


async def verify_dm_11(*, client: Any, inbox: EventInbox, config: EventPlaneConfig | None = None, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
    """DM-11 (/events list source): cross-check the newest /events item against the consumer."""
    return await _audit_crosscheck("DM-11", client=client, inbox=inbox, timeout=timeout)


async def verify_dm_12(*, client: Any, inbox: EventInbox, config: EventPlaneConfig | None = None, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
    """DM-12 (/events-stream): cross-check the newest /events item against the stream consumer."""
    return await _audit_crosscheck("DM-12", client=client, inbox=inbox, timeout=timeout)


async def verify_dm_13(*, client: Any, inbox: EventInbox, config: EventPlaneConfig | None = None, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
    """DM-13 (audit-log webhook): cross-check the newest /events item against the webhook consumer."""
    return await _audit_crosscheck("DM-13", client=client, inbox=inbox, timeout=timeout)


async def verify_dm_14(*, check: Any, **_: Any) -> VerificationReport:
    """DM-14 (audit completeness): fails until an admin has confirmed the exclusions; the detail lists every gap."""
    report: VerificationReport = await check.verify()
    return report


async def _probe_via_inbox(method_id: str, *, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta) -> VerificationReport:
    return await _run(method_id, target=config.probe_issue, target_name="probe_issue", trigger=issue_property_trigger(client), inbox=inbox, timeout=timeout)


async def verify_dm_06(*, verifier: Any, **kw: Any) -> VerificationReport:
    """DM-06 (Forge native): the generated handler's log line must appear in `forge logs`."""
    report: VerificationReport = await verifier.verify(**kw)
    return report


async def verify_dm_07(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
    """DM-07 (Forge inbox): set and clear the probe property; the trigger must reach the inbox and be pulled (Forge delivery can take ~3 minutes)."""
    return await _probe_via_inbox("DM-07", client=client, config=config, inbox=inbox, timeout=timeout)


async def verify_dm_08(*, manager: Any, **_: Any) -> VerificationReport:
    """DM-08 (dynamic triggers): the subscription must be listed by the app."""
    report: VerificationReport = await manager.verify()
    return report


async def verify_dm_09(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
    """DM-09 (Forge Remote): the probe change must reach the Python backend with a valid Forge Invocation Token."""
    return await _probe_via_inbox("DM-09", client=client, config=config, inbox=inbox, timeout=timeout)


async def verify_dm_10(*, panel: Any, **kw: Any) -> VerificationReport:
    """DM-10 (Realtime panel): opened through the browser backend; reports why it cannot run until that backend exists."""
    report: VerificationReport = await panel.verify(**kw)
    return report


async def verify_dm_17(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
    """DM-17 (Automation -> SNS -> SQS): toggle the probe label; the rule publishes, SqsSource must deliver the envelope."""
    return await _run("DM-17", target=config.probe_issue, target_name="probe_issue", trigger=jira_label_trigger(client), inbox=inbox, timeout=timeout)


async def verify_dm_18(*, ssm: Any, command_id: str, instance_id: str, **_: Any) -> VerificationReport:
    """DM-18 (Automation -> SSM): read GetCommandInvocation for the command the rule sent; it must have Succeeded."""
    started = time.monotonic()
    try:
        inv = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
    except Exception as exc:
        return VerificationReport("DM-18", False, None, f"GetCommandInvocation failed: {type(exc).__name__}: {exc}")
    status = inv.get("Status")
    return VerificationReport("DM-18", status == "Success", time.monotonic() - started, f"command status {status}" + (f": {inv.get('StandardErrorContent', '')[:200]}" if status != "Success" else ""))


async def verify_dm_19(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
    """DM-19 (Jira Edge Connector): toggle the probe label; JEC must run the script and the handler must see the event."""
    return await _run("DM-19", target=config.probe_issue, target_name="probe_issue", trigger=jira_label_trigger(client), inbox=inbox, timeout=timeout)


async def _relay_probe(method_id: str, *, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta) -> VerificationReport:
    return await _run(method_id, target=config.probe_issue, target_name="probe_issue", trigger=issue_property_trigger(client), inbox=inbox, timeout=timeout)


async def verify_dm_39(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
    """DM-39 (AWS relay): the probe webhook must pass through API Gateway + Lambda + SQS and reach SqsSource."""
    return await _relay_probe("DM-39", client=client, config=config, inbox=inbox, timeout=timeout)


async def verify_dm_40(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
    """DM-40 (Azure relay): the probe webhook must reach AzureServiceBusSource."""
    return await _relay_probe("DM-40", client=client, config=config, inbox=inbox, timeout=timeout)


async def verify_dm_41(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
    """DM-41 (GCP relay): the probe webhook must reach PubSubSource."""
    return await _relay_probe("DM-41", client=client, config=config, inbox=inbox, timeout=timeout)


async def verify_dm_42(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
    """DM-42 (Cloudflare relay): the probe webhook must reach CloudflareQueuePullSource."""
    return await _relay_probe("DM-42", client=client, config=config, inbox=inbox, timeout=timeout)


async def verify_dm_43(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
    """DM-43 (broker): the probe webhook must reach HttpPullBrokerSource."""
    return await _relay_probe("DM-43", client=client, config=config, inbox=inbox, timeout=timeout)


async def verify_dm_46(*, provisioner: Any, inbox: EventInbox, timeout: timedelta = timedelta(minutes=2), **_: Any) -> VerificationReport:
    """DM-46 (Confluence DC): ask Confluence to send a TEST delivery (no content is changed); some event must arrive in the LAN receiver."""
    start = len(inbox.events)
    sent: VerificationReport = await provisioner.send_test()
    if not sent.ok:
        return sent
    hit = await inbox.wait_for(lambda e: True, timeout.total_seconds(), start=start)
    if hit is None:
        return VerificationReport("DM-46", False, None, "the test delivery did not arrive: check LAN routing/firewall between Confluence and the receiver")
    return VerificationReport("DM-46", True, sent.latency_seconds, f"test delivery arrived ({hit.native_type})")


async def verify_dm_47(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=2)) -> VerificationReport:
    """DM-47 (Jira DC): set and clear the probe property on the sandbox issue; the LAN receiver must see the event."""
    return await _run("DM-47", target=config.probe_issue, target_name="probe_issue", trigger=issue_property_trigger(client), inbox=inbox, timeout=timeout)


# ---------------------------------------------------------------------------------------------------------------------
# P4b: human channels. The probe toggles a label on the sandbox issue/page; the notification must come back on the method's consumer.
# ---------------------------------------------------------------------------------------------------------------------
async def _human_issue(method_id: str, *, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta) -> VerificationReport:
    return await _run(method_id, target=config.probe_issue, target_name="probe_issue", trigger=jira_label_trigger(client), inbox=inbox, timeout=timeout)


async def _human_page(method_id: str, *, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta) -> VerificationReport:
    return await _run(method_id, target=config.probe_page, target_name="probe_page", trigger=confluence_label_trigger(client), inbox=inbox, timeout=timeout)


def _mk_issue(dm: str, doc: str) -> Any:
    async def verify(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=10)) -> VerificationReport:
        return await _human_issue(dm, client=client, config=config, inbox=inbox, timeout=timeout)
    verify.__name__ = f"verify_{dm.lower().replace('-', '_')}"
    verify.__doc__ = doc
    return verify


def _mk_page(dm: str, doc: str) -> Any:
    async def verify(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=10)) -> VerificationReport:
        return await _human_page(dm, client=client, config=config, inbox=inbox, timeout=timeout)
    verify.__name__ = f"verify_{dm.lower().replace('-', '_')}"
    verify.__doc__ = doc
    return verify


verify_dm_20 = _mk_issue("DM-20", "DM-20 (Automation -> email): the rule's email must arrive on the IMAP source after the probe label toggle.")
verify_dm_21 = _mk_issue("DM-21", "DM-21 (Automation -> Slack): the rule's Slack message must arrive on the Socket Mode source.")
verify_dm_22 = _mk_issue("DM-22", "DM-22 (Automation -> Teams): the rule's Teams message must arrive on the Graph polling source.")
verify_dm_23 = _mk_issue("DM-23", "DM-23 (Automation -> Twilio SMS): the SMS must appear in the Twilio message log.")
verify_dm_24 = _mk_issue("DM-24", "DM-24 (other connectors / incoming-webhook trigger): the connector's output must arrive on its consumer.")
verify_dm_25 = _mk_issue("DM-25", "DM-25 (notification scheme): the scheme's email must arrive after the probe label toggle.")
verify_dm_26 = _mk_issue("DM-26", "DM-26 (custom event): the custom-event email must arrive after the transition that fires it.")
verify_dm_27 = _mk_issue("DM-27", "DM-27 (watchers): the watcher email must arrive after the probe change.")
verify_dm_28 = _mk_page("DM-28", "DM-28 (Confluence watches): the watch email (immediate or digest) must arrive after the probe page change.")
verify_dm_29 = _mk_issue("DM-29", "DM-29 (filter subscription): the scheduled digest must list the probe issue (arrives at the subscription time).")
verify_dm_30 = _mk_issue("DM-30", "DM-30 (Jira RSS): the filter feed must contain the probe issue after the label toggle.")
verify_dm_31 = _mk_page("DM-31", "DM-31 (Confluence RSS): the feed must contain the probe page after the change.")
verify_dm_32 = _mk_issue("DM-32", "DM-32 (Jira Cloud for Slack): the Jira app's channel post must arrive after the probe label toggle.")
verify_dm_33 = _mk_page("DM-33", "DM-33 (Confluence + Slack): the Confluence app's channel post must arrive after the probe page change.")
verify_dm_34 = _mk_issue("DM-34", "DM-34 (Jira for Microsoft Teams): the Teams channel message must arrive after the probe label toggle.")


async def verify_dm_35(*, ops: Any, timeout: timedelta = timedelta(minutes=2), **_: Any) -> VerificationReport:
    """DM-35 (JSM Operations): create a clearly-labelled TEST alert, require it to be listed, then close it. Needs the ops client and WRITE confirmation upstream."""
    started = time.monotonic()
    try:
        alert = await ops.create_alert("atlian-assiant verify probe", alias=f"atlian-assiant-probe-{int(started)}", priority="P5")
        listed = await ops.list_alerts(limit=20)
        await ops.close(str(alert["id"]))
    except AtlassianError as exc:
        return VerificationReport("DM-35", False, None, f"Operations API probe failed: {type(exc).__name__}: {exc.message}")
    seen = any(str(a.get("id")) == str(alert["id"]) for a in listed)
    return VerificationReport("DM-35", seen, time.monotonic() - started if seen else None,
                              "probe alert was created, listed and closed" if seen else "the probe alert was not visible in the alert list")


async def verify_dm_04(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
    """DM-04 (Connect webhook): the probe change must arrive with a valid Connect JWT for the tenant."""
    return await _run("DM-04", target=config.probe_issue, target_name="probe_issue", trigger=issue_property_trigger(client), inbox=inbox, timeout=timeout)


async def verify_dm_44(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
    """DM-44 (tunnel): the probe change must reach the local receiver through the tunnel's public URL."""
    return await _run("DM-44", target=config.probe_issue, target_name="probe_issue", trigger=issue_property_trigger(client), inbox=inbox, timeout=timeout)


async def verify_dm_48(*, client: Any, config: EventPlaneConfig, inbox: EventInbox, timeout: timedelta = timedelta(minutes=10)) -> VerificationReport:
    """DM-48 (polling through MCP): toggle the probe label; a poller with transport=MCP must emit the issue."""
    return await _run("DM-48", target=config.probe_issue, target_name="probe_issue", trigger=jira_label_trigger(client), inbox=inbox, timeout=timeout)
