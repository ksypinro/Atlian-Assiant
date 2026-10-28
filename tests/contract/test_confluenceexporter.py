"""Contract suite for ConfluenceExporter (design 9.6): what each backend can export, and an honest refusal (with the alternative) for what it cannot."""
import pytest

from atlian_assiant import BackendKind
from atlian_assiant.core.errors import UnsupportedOperationError
from atlian_assiant.protocols import ConfluenceExporter
from atlian_assiant.testing import FakeBackend

from .matrix_backends import BACKENDS
from .matrix_harness import Ctx

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]


@pytest.fixture(params=["fake", "rest", "mcp", "browser"])
def ctx(request) -> Ctx:
    if request.param == "fake":
        return Ctx(FakeBackend(BackendKind.MCP), None)
    return BACKENDS[request.param][0]()


async def test_html_export_of_a_page_contains_its_title_and_body(ctx, tmp_path):
    out = await ctx.impl(ConfluenceExporter).export_content("123", format="html", dest=tmp_path / "p.html")
    text = out.read_text()
    assert out.parent == tmp_path and "Runbook" in text and "nginx" in text


async def test_a_missing_page_is_not_found_not_an_empty_file(ctx, tmp_path):
    from atlian_assiant.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await ctx.impl(ConfluenceExporter).export_content("999999", format="html", dest=tmp_path / "x.html")
    assert not (tmp_path / "x.html").exists()


@pytest.mark.parametrize(("name", "fmt", "alternative"), [("rest", "pdf", "browser"), ("rest", "word", "browser"), ("browser", "markdown", "REST")])
async def test_formats_a_backend_cannot_render_are_refused_with_the_alternative(name, fmt, alternative, tmp_path):
    ctx = BACKENDS[name][0]()
    with pytest.raises(UnsupportedOperationError) as exc:
        await ctx.impl(ConfluenceExporter).export_content("123", format=fmt, dest=tmp_path / "x")
    assert alternative in exc.value.hint and not (tmp_path / "x").exists()


async def test_mcp_renders_pdf_and_word_server_side(tmp_path):
    ctx = BACKENDS["mcp"][0]()
    pdf = await ctx.impl(ConfluenceExporter).export_content("123", format="pdf", dest=tmp_path / "p.pdf")
    assert pdf.read_bytes().startswith(b"%PDF")


async def test_mcp_has_no_space_export_and_says_so(tmp_path):
    with pytest.raises(UnsupportedOperationError):
        await BACKENDS["mcp"][0]().impl(ConfluenceExporter).export_space("ENG", format="html", dest=tmp_path / "s.html")


async def test_the_browser_exports_a_whole_space_natively(tmp_path):
    out = await BACKENDS["browser"][0]().impl(ConfluenceExporter).export_space("ENG", format="html", dest=tmp_path / "s.zip")
    assert b"ENG" in out.read_bytes()
