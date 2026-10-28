"""Contract suite for JiraExporter (design 9.6): the reference (Fake) and every backend that claims ``export.jira.csv`` behave the same."""
import pytest

from atlian_assiant import BackendKind
from atlian_assiant.protocols import JiraExporter
from atlian_assiant.protocols.base import implementation_of
from atlian_assiant.testing import FakeBackend

from .matrix_backends import BACKENDS
from .matrix_harness import Ctx

pytestmark = [pytest.mark.layer_01, pytest.mark.anyio]
SYNTHESIZED = ["rest", "data_lake"]                    # build the CSV themselves (so they can neutralize formulas); acli / browser print the product's own CSV


@pytest.fixture(params=["fake", "rest", "acli", "browser", "data_lake"])
def ctx(request) -> Ctx:
    if request.param == "fake":
        return Ctx(FakeBackend(BackendKind.REST), None)
    return BACKENDS[request.param][0]()


async def test_the_csv_has_a_header_and_one_row_per_issue(ctx, tmp_path):
    out = await ctx.impl(JiraExporter).export_issues_csv("project = PROJ", dest=tmp_path / "issues.csv")
    lines = out.read_text().splitlines()
    assert lines[0].startswith("key,summary") and any(line.startswith("PROJ-1,Login fails") for line in lines[1:]) and len(lines) >= 4


async def test_it_returns_the_destination_and_creates_missing_directories(ctx, tmp_path):
    dest = tmp_path / "a" / "b" / "issues.csv"
    assert await ctx.impl(JiraExporter).export_issues_csv("project = PROJ", dest=dest) == dest and dest.is_file()


@pytest.mark.parametrize("name", SYNTHESIZED)
async def test_synthesized_exports_neutralize_spreadsheet_formulas(name, tmp_path):
    ctx = BACKENDS[name][0]()
    if name == "rest":
        ctx.server.raw["jira"]["issues"][0]["fields"]["summary"] = "=HYPERLINK(\"http://evil\")"
    else:
        ctx.fake.data[next(t for t in ctx.fake.data if t.endswith("jira_issue"))][0]["summary"] = "=HYPERLINK(\"http://evil\")"
    out = await ctx.impl(JiraExporter).export_issues_csv("project = PROJ", dest=tmp_path / "x.csv")
    text = out.read_text()
    assert "'=HYPERLINK" in text and ",=HYPERLINK" not in text


@pytest.mark.parametrize("name", SYNTHESIZED)
async def test_synthesized_exports_honour_the_requested_columns(name, tmp_path):
    ctx = BACKENDS[name][0]()
    out = await ctx.impl(JiraExporter).export_issues_csv("project = PROJ", fields=["key", "status"], dest=tmp_path / "x.csv")
    lines = out.read_text().splitlines()
    assert lines[0] == "key,status" and lines[1].startswith("PROJ-1,")


def test_the_reference_implementation_is_a_jira_exporter():
    assert implementation_of(FakeBackend(BackendKind.REST), JiraExporter) is not None
