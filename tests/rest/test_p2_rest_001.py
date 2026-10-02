"""Confluence version-checked updates: send current + 1, ConflictError with a re-fetch hint on a lost race (design 10.1)."""
import pytest

from atlassian_client import AtlassianConfig, BackendKind, ClientFactory
from atlassian_client.core.errors import ConflictError
from atlassian_client.models.confluence import ContentUpdate
from atlassian_client.testing.server import ScenarioServer


def writer(server):
    be = ClientFactory().backend(BackendKind.REST, AtlassianConfig(site="example.atlassian.net", email="a@b.c", api_token="tok-123456"), http=server.client())
    return be.confluence_write


@pytest.mark.anyio
async def test_update_sends_current_version_plus_one_and_the_page_advances():
    server = ScenarioServer()
    w = writer(server)
    page = await w.update_content("123", ContentUpdate(body="First edit", message="m1"))
    assert page.version == 4
    put = [r for r in server.requests if r["method"] == "PUT"][-1]
    assert put["path"] == "/wiki/api/v2/pages/123" and put["json"]["version"] == {"number": 4, "message": "m1"}
    page = await w.update_content("123", ContentUpdate(body="Second edit"))
    assert page.version == 5 and [r for r in server.requests if r["method"] == "PUT"][-1]["json"]["version"]["number"] == 5


@pytest.mark.anyio
async def test_stale_expected_version_is_rejected_before_any_write_is_sent():
    server = ScenarioServer()
    w = writer(server)
    with pytest.raises(ConflictError) as exc:
        await w.update_content("123", ContentUpdate(body="based on an old read", version=2))
    assert exc.value.status == 409 and "Re-fetch" in exc.value.hint and "v2" in exc.value.message and "v3" in exc.value.message
    assert not [r for r in server.requests if r["method"] == "PUT"]


@pytest.mark.anyio
async def test_a_409_from_atlassian_becomes_conflict_error_with_the_hint():
    server = ScenarioServer()
    w = writer(server)
    real_get = w._reader.get_content

    async def stale(*a, **k):
        page = await real_get(*a, **k)
        page.version = 1                                  # we think we are at v1, so we send v2; Atlassian is at v3
        return page

    w._reader.get_content = stale
    with pytest.raises(ConflictError) as exc:
        await w.update_content("123", ContentUpdate(body="lost the race"))
    assert exc.value.status == 409 and "current + 1" in exc.value.hint and "Current Version: 3" in exc.value.message


@pytest.mark.anyio
async def test_title_only_updates_keep_the_existing_body_and_updates_are_not_retried_on_503():
    server = ScenarioServer()
    w = writer(server)
    await w.update_content("123", ContentUpdate(title="Renamed"))
    body = [r for r in server.requests if r["method"] == "PUT"][-1]["json"]["body"]
    assert body["representation"] == "storage" and "Restart" in body["value"]
    server.fail_next = [(503, {}, "PUT")]
    from atlassian_client.core.errors import ServerError

    with pytest.raises(ServerError):
        await w.update_content("123", ContentUpdate(body="x"))
    assert len([r for r in server.requests if r["method"] == "PUT"]) == 2        # one successful + exactly one failed attempt: never replayed


@pytest.mark.anyio
async def test_missing_page_is_not_found():
    from atlassian_client.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await writer(ScenarioServer()).update_content("999999", ContentUpdate(body="x"))
