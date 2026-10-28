import pytest

from atlian_assiant.core.errors import InvalidRequestError
from atlian_assiant.models.jira import VersionChange
from atlian_assiant.protocols import JiraProjectReader, JiraProjectWriter

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]


async def test_manage_version_create_release_delete(harness):
    w, r = harness.impl(JiraProjectWriter), harness.impl(JiraProjectReader)
    v = await w.manage_version("PROJ", VersionChange(action="create", name="2.0"))
    assert v.name == "2.0" and v.id
    assert "2.0" in [x.name for x in await r.list_versions("PROJ")]
    if harness.name == "rest":                                   # the fake does not model release state
        rel = await w.manage_version("PROJ", VersionChange(action="release", version_id=v.id, release_date="2026-11-01"))
        assert rel.released is True
    await w.manage_version("PROJ", VersionChange(action="delete", version_id=v.id))
    assert "2.0" not in [x.name for x in await r.list_versions("PROJ")]


async def test_manage_version_needs_an_id_for_changes(harness):
    if harness.name == "fake":
        pytest.skip("the fake accepts any change")
    w = harness.impl(JiraProjectWriter)
    for action in ("update", "release", "delete"):
        with pytest.raises(InvalidRequestError):
            await w.manage_version("PROJ", VersionChange(action=action))
    with pytest.raises(InvalidRequestError):
        await w.manage_version("PROJ", VersionChange(action="explode"))
