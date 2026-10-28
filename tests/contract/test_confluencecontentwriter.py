import pytest

from atlian_assiant.core.errors import ConflictError, InvalidRequestError, NotFoundError, UnsupportedOperationError
from atlian_assiant.models.confluence import ContentDraft, ContentUpdate, Restrictions
from atlian_assiant.protocols import ConfluenceContentReader, ConfluenceContentWriter

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]


async def test_conf_content_write_create_and_read_back(harness):
    w, r = harness.impl(ConfluenceContentWriter), harness.impl(ConfluenceContentReader)
    page = await w.create_content(ContentDraft(title="Fresh page", space="ENG", body="# Hello\n\nWorld **bold**", parent_id="100"))
    assert page.id and page.title == "Fresh page" and page.version == 1
    got = await r.get_content(page.id)
    assert got.title == "Fresh page" and got.body_markdown == "# Hello\n\nWorld **bold**"
    with pytest.raises(NotFoundError):
        await w.create_content(ContentDraft(title="x", space="NOPE", body=""))


async def test_conf_content_write_create_sends_storage_body_and_space_id(harness):
    if harness.server is None:
        pytest.skip("wire-level assertion")
    await harness.impl(ConfluenceContentWriter).create_content(ContentDraft(title="T", space="ENG", body="Hi **there**", parent_id="123"))
    sent = next(x for x in harness.server.requests if (x["method"], x["path"]) == ("POST", "/wiki/api/v2/pages"))
    assert sent["json"]["spaceId"] == "9001" and sent["json"]["parentId"] == "123" and sent["json"]["status"] == "current"
    assert sent["json"]["body"] == {"representation": "storage", "value": "<p>Hi <strong>there</strong></p>"}


async def test_conf_content_write_update_is_version_checked(harness):
    w, r = harness.impl(ConfluenceContentWriter), harness.impl(ConfluenceContentReader)
    updated = await w.update_content("123", ContentUpdate(body="Fresh text", version=3, message="edit"))
    assert updated.version == 4
    assert (await r.get_content("123")).body_markdown == "Fresh text"
    with pytest.raises(ConflictError) as exc:
        await w.update_content("123", ContentUpdate(body="stale", version=3))
    assert "Re-fetch" in exc.value.hint
    again = await w.update_content("123", ContentUpdate(title="Renamed"))                 # no expected version: uses the current one
    assert again.version == 5 and again.title == "Renamed"
    assert (await r.get_content("123")).body_markdown == "Fresh text"                   # body kept when only the title changes


async def test_conf_content_write_update_sends_current_plus_one_and_maps_409(harness):
    if harness.server is None:
        pytest.skip("wire-level assertion")
    w = harness.impl(ConfluenceContentWriter)
    await w.update_content("123", ContentUpdate(body="x"))
    put = next(x for x in harness.server.requests if x["method"] == "PUT")
    assert put["json"]["version"]["number"] == 4 and put["json"]["id"] == "123"
    # simulate someone else editing between our read and our write
    harness.server.raw["confluence"]["pages"]["123"]["version"]["number"] = 9
    orig = w._reader.get_content

    async def stale_read(*a, **k):
        page = await orig(*a, **k)
        page.version = 4
        return page

    w._reader.get_content = stale_read
    with pytest.raises(ConflictError) as exc:
        await w.update_content("123", ContentUpdate(body="y"))
    assert exc.value.status == 409 and "current + 1" in exc.value.hint


async def test_conf_content_write_move_copy_archive_restore(harness):
    w, r = harness.impl(ConfluenceContentWriter), harness.impl(ConfluenceContentReader)
    moved = await w.move_content("125", target_parent_id="100")
    assert moved.parent_id == "100"
    copy = await w.copy_content("123", destination_parent_id="100", title="Runbook copy")
    assert copy.title == "Runbook copy" and copy.id != "123"
    await w.archive_content(["124"])
    assert (await r.get_content("124")).status == "archived"
    restored = await w.restore_version("123", 2, message="back")
    assert restored.body_markdown == "# Runbook\n\nRestart nginx"
    with pytest.raises(InvalidRequestError):
        await w.move_content("125")
    with pytest.raises(NotFoundError):
        await w.archive_content(["999999"])


async def test_conf_content_write_labels_comments_properties_tasks(harness):
    w, r = harness.impl(ConfluenceContentWriter), harness.impl(ConfluenceContentReader)
    labels = await w.add_labels("123", ["alpha", "beta"])
    assert {lb.name for lb in labels} >= {"alpha", "beta"}
    await w.remove_label("123", "alpha")
    assert {lb.name for lb in await r.list_labels("123")} == {"runbook", "beta"}
    c = await w.add_comment("123", "Nice **work**")
    assert c.kind == "footer" and c.body_markdown == "Nice **work**"
    inline = await w.add_comment("123", "Typo", inline_anchor={"textSelection": "x", "textSelectionMatchCount": 1, "textSelectionMatchIndex": 0})
    assert inline.kind == "inline"
    edited = await w.update_comment(c.id, "Edited")
    assert edited.body_markdown == "Edited"
    await w.resolve_comment(inline.id, resolved=True)
    got = {x.id: x for x in (await r.list_comments("123")).items}
    assert got[inline.id].resolved is True and got[c.id].body_markdown == "Edited"
    await w.set_property("123", "app.new", {"v": 1})
    await w.set_property("123", "app.new", {"v": 2})
    assert (await r.get_properties("123"))["app.new"] == {"v": 2}
    t = await w.set_task_status("t1", complete=True)
    assert t.status == "complete"
    with pytest.raises(NotFoundError):
        await w.update_comment("nope", "x")


async def test_conf_content_write_attachments_upload_and_download(harness):
    w, r = harness.impl(ConfluenceContentWriter), harness.impl(ConfluenceContentReader)
    att = await w.upload_attachment("123", b"report body", filename="report.txt", comment="v1")
    assert att.title == "report.txt" and att.page_id == "123"
    got = await r.download_attachment(att.id)
    assert got.data == b"report body"
    assert "report.txt" in [a.title for a in (await r.list_attachments("123")).items]
    if harness.server is not None:
        sent = next(x for x in harness.server.requests if x["path"].endswith("/child/attachment"))
        assert sent["headers"]["x-atlassian-token"] == "nocheck"


async def test_conf_content_write_restrictions_and_watch(harness):
    w, r = harness.impl(ConfluenceContentWriter), harness.impl(ConfluenceContentReader)
    await w.set_restrictions("124", Restrictions(read_groups=["admins"], update_groups=["admins"]))
    res = await r.get_restrictions("124")
    assert res.read_groups == ["admins"] and res.update_groups == ["admins"] and res.restricted
    await w.watch(content_id="123", watch=True)
    await w.watch(space_key="ENG", watch=False)
    with pytest.raises(InvalidRequestError):
        await w.watch()


async def test_conf_content_write_features_without_a_rest_endpoint_are_unsupported_on_rest(harness):
    if harness.name != "rest":
        pytest.skip("the fake models everything")
    w = harness.impl(ConfluenceContentWriter)
    for call in (w.unarchive_content(["123"]), w.set_status("123", "Ready"), w.star(content_id="123"), w.add_reaction("123", "thumbsup")):
        with pytest.raises(UnsupportedOperationError) as exc:
            await call
        assert "MCP" in exc.value.hint
