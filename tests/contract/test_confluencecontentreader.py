import pytest

from atlassian_client.core.errors import NotFoundError
from atlassian_client.protocols import ConfluenceContentReader

from .helpers import drain

pytestmark = [pytest.mark.layer_01]


@pytest.mark.anyio
async def test_conf_content_get_converts_storage_to_markdown(impl):
    page = await impl(ConfluenceContentReader).get_content("123")
    assert (page.id, page.title, page.version, page.space_id, page.parent_id) == ("123", "Runbook", 3, "9001", "100")
    assert page.body_markdown == "# Runbook\n\nRestart **nginx**"
    assert page.body_storage and page.body_storage.startswith("<h1>")
    assert page.url == "https://example.atlassian.net/wiki/spaces/ENG/pages/123/Runbook"


@pytest.mark.anyio
async def test_conf_content_get_body_none_missing_and_listing(impl):
    r = impl(ConfluenceContentReader)
    assert (await r.get_content("123", body_format="none")).body_markdown is None
    with pytest.raises(NotFoundError):
        await r.get_content("999999")
    pages = await drain(r.list_content, limit=2)
    assert sum(len(p.items) for p in pages) == 4 and len(pages) == 2
    assert [p.title for p in (await r.list_content(title="Home")).items] == ["Home"]


@pytest.mark.anyio
async def test_conf_tree_children_ancestors_descendants(impl):
    r = impl(ConfluenceContentReader)
    pages = await drain(r.list_children, "123", limit=1)
    assert [c.id for p in pages for c in p.items] == ["124", "125"] and len(pages) == 2
    assert [a.id for a in await r.list_ancestors("123")] == ["100"]
    assert {d.id for d in (await r.list_descendants("123", depth=2)).items} == {"124", "125"}


@pytest.mark.anyio
async def test_conf_versions_diff_between_two_versions(impl):
    r = impl(ConfluenceContentReader)
    versions = (await r.list_versions("123")).items
    assert [v.number for v in versions] == [3, 2]
    diff = await r.diff_versions("123", 2, 3)
    assert diff.from_version == 2 and diff.to_version == 3
    assert "-Restart nginx" in diff.unified and "+Restart **nginx**" in diff.unified
    assert (await r.get_content("123", version=2)).body_markdown == "# Runbook\n\nRestart nginx"


@pytest.mark.anyio
async def test_conf_comments_attachments_labels(impl, tmp_path):
    r = impl(ConfluenceContentReader)
    comments = (await r.list_comments("123")).items
    assert {(c.kind, c.body_markdown) for c in comments} == {("footer", "Thanks"), ("inline", "Typo here")}
    assert [c.kind for c in (await r.list_comments("123", kind="inline")).items] == ["inline"]
    atts = (await r.list_attachments("123")).items
    assert [a.title for a in atts] == ["diagram.png", "notes.txt"]
    got = await r.download_attachment("cat2", extract_text=True)
    assert got.data == b"notes" and got.text == "notes"
    await r.download_attachment("cat1", dest=tmp_path / "d.png")
    assert (tmp_path / "d.png").read_bytes() == b"\x89PNG"
    assert [lb.name for lb in await r.list_labels("123")] == ["runbook"]
    assert await r.get_properties("123") == {"app.state": {"ok": True}}


@pytest.mark.anyio
async def test_conf_permissions_and_restrictions(impl):
    r = impl(ConfluenceContentReader)
    assert {o["operation"] for o in (await r.get_permissions("123")).operations} == {"read", "update"}
    res = await r.get_restrictions("123")
    assert res.restricted and [u.display_name for u in res.read_users] == ["Ann Lee"] and res.read_groups == ["eng"]


@pytest.mark.anyio
async def test_conf_tasks_templates_reactions_tasks_filtering(impl):
    r = impl(ConfluenceContentReader)
    assert {t.id for t in (await r.list_tasks(content_id="123")).items} == {"t1", "t2"}
    open_tasks = (await r.list_tasks(status="incomplete")).items
    assert [(t.id, t.body) for t in open_tasks] == [("t1", "Rotate keys")]


@pytest.mark.anyio
async def test_conf_macros_resolve_returns_content(impl):
    page = await impl(ConfluenceContentReader).resolve_macros("123")
    assert page.id == "123" and "Restart" in (page.body_markdown or "")
