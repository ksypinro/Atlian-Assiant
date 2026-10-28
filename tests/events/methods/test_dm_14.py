"""DM-14: audit exclusions and completeness (AuditCompletenessCheck guided; gap reports and describe() warnings)."""
from __future__ import annotations

import pytest

from atlian_assiant.events.catalog import Ingress, method
from atlian_assiant.events.provision.base import EventProvisioner, ProvisionMode
from atlian_assiant.events.provision.org_audit import AUDIT_WARNING, AuditCompletenessCheck, AuditGapReport
from atlian_assiant.events.verify import verify_dm_14
from atlian_assiant.policy import CallbackConfirmation, DenyAll

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)


def test_dm_14_catalog_entry():
    m = method("DM-14")
    assert m.phase == "P4" and m.ingress is Ingress.NOT_APPLICABLE and isinstance(AuditCompletenessCheck(), EventProvisioner)
    assert AuditGapReport.__name__ == m.consumer.split(":")[1]


def test_dm_14_gap_report_warns_until_an_admin_confirms_and_lists_every_exclusion():
    unknown = AuditCompletenessCheck().report()
    assert AUDIT_WARNING in unknown.warnings and any("not been confirmed" in w for w in unknown.warnings) and any("entitlements" in w for w in unknown.warnings)
    known = AuditCompletenessCheck(excluded_categories=("login", "api_token"), entitlements=("Guard Standard",), confirmed_by_admin=True).report()
    assert not any("not been confirmed" in w for w in known.warnings) and "events in category 'login' are excluded from the audit log" in known.warnings
    assert known.excluded_categories == ("login", "api_token")


async def test_dm_14_provision_is_guided_only_and_records_what_the_admin_confirmed():
    check = AuditCompletenessCheck(excluded_categories=("login",), entitlements=("Guard",), confirmed_by_admin=True)
    plan = await check.plan()
    assert plan.method_id == "DM-14" and all(s.mode is ProvisionMode.GUIDED and s.needs_admin for s in plan.steps)
    out = await check.apply(plan, confirm=APPROVE)
    assert out == {"excluded": "login", "entitlements": "Guard", "confirmed": "true"}
    with pytest.raises(Exception, match="declined"):
        await check.apply(plan, confirm=DenyAll())


async def test_dm_14_verify_fails_until_confirmed_and_shows_the_gaps():
    assert not (await AuditCompletenessCheck().verify()).ok
    report = await verify_dm_14(check=AuditCompletenessCheck(excluded_categories=("login",), entitlements=("Guard",), confirmed_by_admin=True))
    assert report.ok and report.method_id == "DM-14" and "login" in report.detail
