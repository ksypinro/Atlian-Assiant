"""P6.QA.001: the operation-coverage report (curated vs generated) is generated, current and honest."""
from tools import codegen


def test_the_committed_report_is_current():
    assert codegen.REPORT.read_text(encoding="utf-8") == codegen.coverage_markdown()


def test_the_report_shows_curated_and_generated_coverage_per_api():
    c = codegen.coverage()
    assert c["curated_operations"] >= 200 and c["generated_operations"] >= 40
    assert sum(r["total"] for r in c["by_api"].values()) == c["generated_operations"]
    assert all(0 <= r["curated"] <= r["total"] for r in c["by_api"].values())
    assert any(r["curated"] for r in c["by_api"].values()), "the path matcher found no curated overlap: the heuristic is broken"
    text = codegen.coverage_markdown()
    assert "Generated only" in text and "heuristic" in text
