"""Attachment upload / download: lazy, streamed, size-capped, text-extractable (design 9.2 and 10.1)."""
import pytest

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.testing.server import ScenarioServer

BODY = b"line one\nline two\n"


def rest(server):
    return ClientFactory().backend(BackendKind.REST, AtlassianConfig(site="example.atlassian.net", email="a@b.c", api_token="tok-123456"), http=server.client())


@pytest.mark.anyio
async def test_upload_from_a_path_or_bytes_is_multipart_with_the_no_check_header(tmp_path):
    server = ScenarioServer()
    be = rest(server)
    f = tmp_path / "report.txt"
    f.write_bytes(BODY)
    a = await be.jira_write.upload_attachment("PROJ-2", f)
    b = await be.jira_write.upload_attachment("PROJ-2", b"raw bytes", filename="b.bin")
    c = await be.confluence_write.upload_attachment("123", f, comment="v1")
    assert (a[0].filename, b[0].filename, c.title) == ("report.txt", "b.bin", "report.txt")
    uploads = [r for r in server.requests if r["method"] == "POST" and "attach" in r["path"]]
    assert [u["headers"]["x-atlassian-token"] for u in uploads] == ["no-check", "no-check", "nocheck"]
    assert all(u["headers"]["content-type"].startswith("multipart/form-data; boundary=") for u in uploads)
    assert server.raw["jira"]["attachment_content"][a[0].id] == BODY and server.raw["confluence"]["attachment_content"][c.id] == BODY


@pytest.mark.anyio
async def test_download_is_lazy_it_fetches_metadata_first_and_content_only_when_asked():
    server = ScenarioServer()
    be = rest(server)
    atts = await be.jira.list_attachments("PROJ-1")
    assert atts and not any("/attachment/content/" in r["path"] for r in server.requests)
    got = await be.jira.download_attachment(atts[0].id)
    assert got.data == b"hello" and any("/attachment/content/att1" in r["path"] for r in server.requests)


@pytest.mark.anyio
async def test_max_bytes_is_enforced_from_metadata_and_from_the_actual_body():
    server = ScenarioServer()
    be = rest(server)
    with pytest.raises(Exception, match="max_bytes"):
        await be.jira.download_attachment("att1", max_bytes=2)
    assert not any("/attachment/content/" in r["path"] for r in server.requests)          # refused before downloading
    # metadata understates the size: the streamed body is still capped
    server.raw["jira"]["issues"][0]["fields"]["attachment"][0]["size"] = 1
    with pytest.raises(Exception, match="max_bytes"):
        await be.jira.download_attachment("att1", max_bytes=3)
    with pytest.raises(Exception, match="max_bytes"):
        await be.confluence.download_attachment("cat2", max_bytes=3)
    assert (await be.jira.download_attachment("att1", max_bytes=5)).size == 5


@pytest.mark.anyio
async def test_text_extraction_only_for_text_files_and_save_to_disk(tmp_path):
    server = ScenarioServer()
    be = rest(server)
    text = await be.confluence.download_attachment("cat2", extract_text=True)
    png = await be.confluence.download_attachment("cat1", extract_text=True)
    assert text.text == "notes" and png.text is None and png.mime_type == "image/png"
    saved = await be.confluence.download_attachment("cat2", dest=tmp_path / "deep" / "dir" / "n.txt")
    assert saved.data is None and saved.path and (tmp_path / "deep" / "dir" / "n.txt").read_bytes() == b"notes"


@pytest.mark.anyio
async def test_uploads_are_serialized_per_issue_and_never_retried_on_a_server_error():
    from atlian_assiant.core.errors import ServerError

    server = ScenarioServer()
    be = rest(server)
    server.fail_next = [(503, {})]
    with pytest.raises(ServerError):
        await be.jira_write.upload_attachment("PROJ-2", b"x", filename="a.txt")
    assert len([r for r in server.requests if r["method"] == "POST" and r["path"].endswith("/attachments")]) == 1
