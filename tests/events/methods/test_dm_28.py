"""DM-28: Confluence page / space / blog watches, immediate + digest (WatchProvisioner(product="confluence"), ImapSource, verify_dm_28)."""
from __future__ import annotations

import pytest

from atlian_assiant.events.catalog import method
from atlian_assiant.events.provision.base import ProvisionMode
from atlian_assiant.events.provision.jira_notifications import WatchProvisioner
from atlian_assiant.events.verify import verify_dm_28
from atlian_assiant.policy import CallbackConfirmation

from ..conftest import make_client
from .humancases import check_consumer, check_digest, check_security_and_dedup, check_verify
from .humansim import SITE

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]
APPROVE = CallbackConfirmation(lambda plan: True)


def test_dm_28_catalog_entry():
    m = method("DM-28")
    assert m.provisioner.endswith("WatchProvisioner") and {p.value for p in m.products} == {"confluence"}


@pytest.mark.parametrize("kind", ["content", "space", "label"])
async def test_dm_28_provision_watches_content_spaces_and_labels_through_the_facade(kind):
    client = make_client()
    prov = WatchProvisioner(client, product="confluence", targets=["123" if kind == "content" else "ENG"], kind=kind)
    plan = await prov.plan()
    assert plan.method_id == "DM-28" and any("digest mail is split" in w for w in plan.warnings)
    out = await prov.apply(plan, confirm=APPROVE)
    assert out["watching"] and len(client.seen) == 1
    await prov.teardown(confirm=APPROVE)
    assert prov.done == []


async def test_dm_28_provision_guided_mentions_immediate_and_digest_settings():
    prov = WatchProvisioner(None, product="confluence", targets=["123"])
    out = await prov.apply(await prov.plan(mode=ProvisionMode.GUIDED), confirm=APPROVE)
    assert "immediate or digest" in out["instructions"]
    with pytest.raises(Exception, match="watch kind"):
        WatchProvisioner(None, product="confluence", kind="galaxy")


async def test_dm_28_consumer_splits_digests_and_verify():
    await check_consumer("DM-28", subject="[Confluence] Runbook updated", body=f"{SITE}/browse/PROJ-1")
    await check_security_and_dedup("DM-28")
    await check_digest("DM-28", f"Digest {SITE}/wiki/spaces/ENG/pages/11/A and {SITE}/wiki/spaces/ENG/pages/12/B and {SITE}/wiki/spaces/ENG/pages/13/C")
    await check_verify(verify_dm_28, "DM-28", page=True)
