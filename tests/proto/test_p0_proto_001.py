import ast
import inspect
import re
from pathlib import Path

import pytest

import atlian_assiant.protocols as P

DESIGN = Path(__file__).parents[2] / "docs/architecture_design.md"


def design_protocols() -> dict[str, dict[str, list[str]]]:
    """{class: {method: [parameter names]}} parsed from section 9 of the design document."""
    text = DESIGN.read_text()
    section = text[text.index("## 9. Protocols"):text.index("## 10. Backends")]
    out: dict[str, dict[str, list[str]]] = {}
    for block in re.findall(r"```python\n(.*?)```", section, re.S):
        for node in ast.parse(block).body:
            if isinstance(node, ast.ClassDef) and any(isinstance(b, ast.Name) and b.id == "Protocol" for b in node.bases):
                out[node.name] = {m.name: [a.arg for a in m.args.args + m.args.kwonlyargs if a.arg != "self"]
                                  + [x.arg for x in (m.args.vararg, m.args.kwarg) if x is not None]
                                  for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return out


DESIGNED = design_protocols()


def test_design_has_the_expected_number_of_protocols():
    assert len(DESIGNED) >= 33 and {"JiraIssueReader", "ConfluenceContentReader", "Backend", "WebhookManager"} <= set(DESIGNED)


@pytest.mark.parametrize("name", sorted(DESIGNED))
def test_protocol_matches_design_exactly(name):
    cls = getattr(P, name)
    assert getattr(cls, "_is_runtime_protocol", False) or getattr(cls, "__protocol_attrs__", None) is not None
    actual = {n: [p for p in inspect.signature(m).parameters if p != "self"]
              for n, m in inspect.getmembers(cls, inspect.isfunction) if not n.startswith("_")}
    assert actual == DESIGNED[name]


@pytest.mark.parametrize("name", sorted(set(DESIGNED) - {"Backend"}))
def test_protocol_is_runtime_checkable_and_methods_are_async(name):
    cls = getattr(P, name)
    class Empty: ...
    assert not isinstance(Empty(), cls)                       # isinstance works (runtime_checkable) and is structural
    for n, m in inspect.getmembers(cls, inspect.isfunction):
        if not n.startswith("_") and name != "RawRequester":
            assert inspect.iscoroutinefunction(m) or name in {"Backend"}, f"{name}.{n} must be async"


def test_every_protocol_has_an_implementation_in_the_fake_backend():
    from atlian_assiant.testing import FakeBackend

    fb = FakeBackend()
    missing = [n for n in DESIGNED if n != "Backend" and fb.implementation_for(getattr(P, n)) is None]
    assert missing == []


def test_backend_protocol_surface():
    from atlian_assiant.testing import FakeBackend

    assert isinstance(FakeBackend(), P.Backend) and set(DESIGNED["Backend"]) == {"supported_operations", "health", "aclose"}
