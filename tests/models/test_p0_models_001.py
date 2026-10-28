import ast
import re
from datetime import UTC
from pathlib import Path

from atlian_assiant.models import admin, common, confluence, graph, identity, jira, jsm, webhooks
from atlian_assiant.models.common import Page, SourceRef, Tombstone, UserRef

DESIGN = Path(__file__).parents[2] / "docs/architecture_design.md"
MODULES = {"admin": admin, "common": common, "confluence": confluence, "graph": graph, "identity": identity, "jira": jira, "jsm": jsm,
           "webhooks": webhooks}


def test_every_model_the_protocols_import_exists():
    text = DESIGN.read_text()
    section = text[text.index("## 9. Protocols"):text.index("## 10. Backends")]
    missing = []
    for block in re.findall(r"```python\n(.*?)```", section, re.S):
        for node in ast.walk(ast.parse(block)):
            if isinstance(node, ast.ImportFrom) and node.level == 2 and node.module and node.module.startswith("models."):
                mod = MODULES[node.module.split(".", 1)[1]]
                missing += [f"{node.module}.{a.name}" for a in node.names if not hasattr(mod, a.name)]
    assert missing == []


def test_models_tolerate_new_api_fields_and_keep_raw_out_of_dumps():
    u = UserRef(account_id="a", display_name="Ann", brand_new_field=1, raw={"secret": "x"})
    assert u.brand_new_field == 1                             # extra="allow": forward compatible
    assert "raw" not in u.model_dump() and "source" in u.model_dump()


def test_page_has_more_semantics():
    assert Page[int](items=[1], next_cursor="c").has_more and not Page[int](items=[1]).has_more


def test_issue_renders_compactly_for_llms_with_truncation_marker():
    i = jira.Issue(id="1", key="P-1", summary="Login fails", status=jira.Status(id="1", name="Done"), assignee=UserRef(display_name="Ann"),
                   description_markdown="d" * 500, labels=["a", "b"])
    text = i.to_agent_text(max_chars=10_000)
    assert text.startswith("**P-1** Login fails") and "- Status: Done" in text and "- Assignee: Ann" in text and "- Labels: a, b" in text
    short = i.to_agent_text(max_chars=60)
    assert len(short) < 120 and "…[truncated" in short


def test_generic_renderer_names_the_object_and_skips_empty_values():
    text = jira.Project(id="1", key="PROJ", name="Project").to_agent_text()
    assert text.startswith("**Project** PROJ") and "- name: Project" in text and "lead" not in text


def test_confluence_restrictions_property_and_tombstone_and_source():
    assert not confluence.Restrictions().restricted and confluence.Restrictions(read_groups=["eng"]).restricted
    t = Tombstone(entity_type="issue", entity_id="PROJ-1")
    assert t.reason == "not_found"
    from datetime import datetime

    from atlian_assiant.capabilities import BackendKind
    ref = SourceRef(backend=BackendKind.REST, fetched_at=datetime.now(UTC))
    assert ref.backend is BackendKind.REST


def test_body_input_accepts_markdown_default_and_adf_dict():
    assert common.BodyInput(value="x").format == "markdown"
    assert common.BodyInput(format="adf", value={"type": "doc"}).value == {"type": "doc"}


def test_content_normalizes_all_three_body_formats():
    from atlian_assiant.backends.rest_cloud import normalize as n

    adf = '{"version":1,"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"Hi"}]}]}'
    c = n.content({"id": "1", "body": {"atlas_doc_format": {"value": adf}}})
    assert c.body_markdown == "Hi" and c.body_adf and c.body_storage is None
    s = n.content({"id": "2", "body": {"storage": {"value": "<p>Hi <em>x</em></p>"}}})
    assert s.body_markdown == "Hi *x*" and s.body_storage
    v = n.content({"id": "3", "body": {"view": {"value": "<p>rendered</p>"}}})
    assert v.body_markdown == "rendered"
    assert n.content({"id": "4"}).body_markdown is None
