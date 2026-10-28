
import pytest

from atlian_assiant.formats import (
    adf_to_markdown,
    adf_to_text,
    markdown_to_adf,
    markdown_to_storage,
    storage_to_markdown,
    to_agent_text,
    truncate,
)

pytestmark = [pytest.mark.layer_08]

MD = ("# Title\n\nHello **bold** and *it* and `code` [link](http://x.io) @[Ann](acc-1)\n\n- a\n  - b\n- c\n\n1. one\n2. two\n\n"
      "```py\nx = 1\n```\n\n> quoted\n\n| a | b |\n|---|---|\n| 1 | 2 |")


def test_adf_markdown_round_trip():
    assert adf_to_markdown(markdown_to_adf(MD)) == MD


def test_adf_to_markdown_handles_real_adf_and_passes_strings_through():
    doc = {"version": 1, "type": "doc", "content": [
        {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "H"}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "see "}, {"type": "mention", "attrs": {"id": "acc-9", "text": "@Bob"}},
                                          {"type": "hardBreak"}, {"type": "inlineCard", "attrs": {"url": "https://x"}}]}]}
    assert adf_to_markdown(doc) == "## H\n\nsee @[Bob](acc-9)  \n<https://x>"
    assert adf_to_markdown("plain wiki text") == "plain wiki text" and adf_to_markdown(None) == ""


def test_unknown_adf_nodes_are_preserved_losslessly():
    node = {"type": "extension", "attrs": {"extensionKey": "k"}, "content": []}
    md = adf_to_markdown({"version": 1, "type": "doc", "content": [node]})
    assert "```adf-node" in md and markdown_to_adf(md)["content"] == [node]


def test_adf_to_text_drops_formatting():
    assert adf_to_text(markdown_to_adf("# T\n\n**b** @[Ann](x) [l](http://u)")) == "T\n\nb @Ann l"


def test_storage_round_trip_with_macros_preserved_byte_for_byte():
    st = ('<h1>T</h1><p>Hi <strong>b</strong> <a href="http://x">l</a></p>'
          '<ac:structured-macro ac:name="info"><ac:rich-text-body><p>keep &amp; me</p></ac:rich-text-body></ac:structured-macro>'
          "<ul><li>a</li><li>b</li></ul><ol><li>x</li></ol><pre>code &lt;1&gt;</pre>")
    md = storage_to_markdown(st)
    assert "confluence-macro:info:" in md and "keep" not in md.split("confluence-macro")[0]
    assert markdown_to_storage(md) == st


def test_storage_tables_and_empty_input():
    st = "<table><tbody><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></tbody></table>"
    md = storage_to_markdown(st)
    assert md == "| a | b |\n|---|---|\n| 1 | 2 |" and markdown_to_storage(md) == st
    assert storage_to_markdown(None) == "" and storage_to_markdown("") == ""


def test_truncate_marks_how_much_was_dropped():
    assert truncate("abc", 10) == ("abc", False)
    text, cut = truncate("x" * 100, 40)
    assert cut and text.endswith("…[truncated 60 chars]") and text.startswith("x" * 40)
    assert truncate("x" * 5, None) == ("x" * 5, False)


def test_to_agent_text_uses_model_renderer_and_truncates():
    from atlian_assiant.models.jira import Issue

    i = Issue(id="1", key="P-1", summary="S" * 50, description_markdown="d" * 200)
    out = to_agent_text(i, max_chars=60)
    assert out.startswith("**P-1**") and "truncated" in out
    assert "truncated" in to_agent_text(object(), 5)
