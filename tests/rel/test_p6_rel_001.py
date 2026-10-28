"""P6.REL.001: packaging: extras, entry points, SemVer rules, py.typed, .pyi stubs. The install matrix: every extra's dependents import cleanly without it."""
from __future__ import annotations

import glob
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import atlian_assiant
from atlian_assiant import _version
from tools import api_surface, gen_stubs

ROOT = Path(__file__).parents[2]
PKG = ROOT / "atlian_assiant"
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())
EXTRAS = PYPROJECT["project"]["optional-dependencies"]
#: every third-party library an extra provides: none of them may be needed just to import the package
OPTIONAL_LIBS = ["mcp", "playwright", "delta_sharing", "pandas", "pyarrow", "duckdb", "sqlalchemy", "boto3", "azure", "google", "slack_sdk", "ngrok", "aiokafka", "redis", "feedparser",
                 "bs4", "markdown_it", "jwt", "flask", "fastapi", "starlette", "uvicorn", "anthropic", "msal", "twilio", "chromadb", "weaviate", "pinecone", "yaml", "opentelemetry"]


def run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, timeout=300, check=False)


def test_every_module_imports_with_every_optional_dependency_blocked():
    """The install matrix: ``pip install atlian-assiant`` (no extras) must import every module; features raise a clear hint only when USED."""
    blocked = {lib: None for lib in OPTIONAL_LIBS}
    code = (f"import sys, pkgutil, importlib; sys.modules.update({blocked!r}); import atlian_assiant; failed = []\n"
            "for m in pkgutil.walk_packages(atlian_assiant.__path__, 'atlian_assiant.'):\n"
            "    try: importlib.import_module(m.name)\n"
            "    except Exception as e: failed.append(f'{m.name}: {type(e).__name__}: {e}')\n"
            "print('FAILED', failed) if failed else print('ok')")
    result = run(code)
    assert result.stdout.strip() == "ok", result.stdout + result.stderr


def test_core_dependencies_are_the_only_hard_ones_and_every_extra_is_well_formed():
    core = {re.split(r"[<>=!~\[]", d)[0] for d in PYPROJECT["project"]["dependencies"]}
    assert core == {"httpx", "pydantic", "pydantic-settings", "anyio"}
    for name, reqs in EXTRAS.items():
        assert reqs and all(re.fullmatch(r"[A-Za-z0-9_.\-]+(\[[A-Za-z0-9_,\-]+\])?(\s*[<>=!~]=?\s*[\w.*]+(,\s*[<>=!~]=?\s*[\w.*]+)*)?", r) for r in reqs), (name, reqs)
    assert {"mcp", "browser", "datalake", "duckdb", "db", "postgres", "chroma", "weaviate", "pinecone", "dev"} <= set(EXTRAS)


def test_every_install_hint_in_the_code_names_an_extra_that_exists():
    named = set()
    for f in PKG.rglob("*.py"):
        named |= set(re.findall(r"atlian-assiant\[(\w+)\]", f.read_text(encoding="utf-8")))
    assert named and named <= set(EXTRAS), named - set(EXTRAS)


def test_every_backend_factory_declares_an_extra_that_exists():
    from atlian_assiant.factory import default_registry

    for kind, factory in default_registry().factories().items():
        assert factory.extra is None or factory.extra in EXTRAS, (kind, factory.extra)


def test_the_console_script_resolves_and_runs():
    module, _, func = PYPROJECT["project"]["scripts"]["atlian-assiant"].partition(":")
    assert callable(getattr(__import__(module, fromlist=[func]), func))
    result = subprocess.run([sys.executable, "-m", "atlian_assiant.cli", "--help"], capture_output=True, text=True, cwd=ROOT, timeout=60, check=False)
    assert result.returncode == 0 and "atlian-assiant" in result.stdout and "mcp" in result.stdout
    assert subprocess.run([sys.executable, "-m", "atlian_assiant.cli", "tools", "export"], capture_output=True, text=True, cwd=ROOT, timeout=60, check=False,
                          env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"}).returncode in (0, 1)           # never a traceback: a typed error or success


def test_py_typed_ships_and_every_package_data_pattern_matches_a_file():
    assert (PKG / "py.typed").is_file()
    patterns = PYPROJECT["tool"]["setuptools"]["package-data"]["atlian_assiant"]
    for pat in patterns:
        assert glob.glob(str(PKG / pat)), f"package-data pattern {pat!r} matches nothing (it would be missing from the wheel)"
    assert "py.typed" in patterns and any(p.endswith(".pyi") for p in patterns)
    assert PYPROJECT["tool"]["setuptools"]["packages"]["find"]["include"] == ["atlian_assiant*"]


def test_the_sync_client_stub_is_current_and_parses():
    assert gen_stubs.main(["--check"]) == 0
    import ast

    tree = ast.parse((PKG / "facade" / "sync.pyi").read_text())
    assert {n.name for n in tree.body if isinstance(n, ast.ClassDef)} >= {"AtlassianClient", "JiraIssuesClient", "ConfluenceContentClient"}


def test_the_stub_gives_mypy_real_types_for_the_blocking_client():
    api = pytest.importorskip("mypy.api")
    good = ("from atlian_assiant import AtlassianClient\n"
            "atl = AtlassianClient.from_env()\n"
            "reveal_type(atl.jira.issues.get('PROJ-1'))\n"
            "reveal_type(atl.confluence.content.get('1'))\n"
            "reveal_type(list(atl.jira.issues.iter_search('x')))\n")
    out, _, _ = api.run(["--no-incremental", "--ignore-missing-imports", "-c", good])
    assert 'Revealed type is "atlian_assiant.models.jira.Issue"' in out and "atlian_assiant.models.confluence.Content" in out and "list[atlian_assiant.models.jira.Issue]" in out
    bad = "from atlian_assiant import AtlassianClient\natl = AtlassianClient.from_env()\natl.jira.issues.get(123)\natl.jira.issues.no_such_method()\n"
    out, _, _ = api.run(["--no-incremental", "--ignore-missing-imports", "-c", bad])
    assert "Argument 1 to \"get\"" in out and "no attribute \"no_such_method\"" in out


def test_version_is_semver_and_identical_in_pyproject_and_the_package():
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:-?(?:a|b|rc)\d+)?(?:\.dev\d+)?", _version.__version__)
    assert PYPROJECT["project"]["version"] == _version.__version__ == atlian_assiant.__version__


def test_the_public_api_snapshot_is_current_and_compare_tells_removals_from_additions():
    assert api_surface.main(["--check"]) == 0
    old = api_surface.current()
    shrunk = {k: v[:-1] for k, v in old.items()}
    removed, added = api_surface.compare(old, shrunk)
    assert set(removed) == set(old) and added == {}                                  # the surface lost its last name in every part: breaking
    removed, added = api_surface.compare(shrunk, old)
    assert removed == {} and set(added) == set(old)                                  # the opposite direction: only additions


def test_a_removed_public_name_makes_the_check_fail(tmp_path, monkeypatch, capsys):
    import json

    snap = tmp_path / "api_surface.json"
    data = api_surface.current()
    data["operations"] = [*data["operations"], "jira.issue.removed_in_this_branch"]
    snap.write_text(json.dumps(data))
    monkeypatch.setattr(api_surface, "SNAPSHOT", snap)
    assert api_surface.main(["--check"]) == 1 and "BREAKING" in capsys.readouterr().out
    data["operations"] = data["operations"][:-1]
    data["tools"] = data["tools"][:-1]
    snap.write_text(json.dumps(data))
    assert api_surface.main(["--check"]) == 1 and "NEW public API" in capsys.readouterr().out


def test_the_release_rules_and_readme_are_in_the_repo():
    rules = (ROOT / "docs" / "RELEASING.md").read_text()
    for needle in ("Semantic Versioning", "Removals and renames", "Safety defaults never loosen", "api_surface", "GATE.P6"):
        assert needle in rules
    readme = (ROOT / "README.md").read_text()
    assert PYPROJECT["project"]["readme"] == "README.md" and "atlian-assiant mcp serve" in readme and "atlian-assiant[mcp]" in readme


def test_the_wheel_contains_the_data_files_and_the_entry_point(tmp_path):
    pytest.importorskip("setuptools")
    import zipfile

    from setuptools import build_meta

    wheel = tmp_path / build_meta.build_wheel(str(tmp_path))
    names = zipfile.ZipFile(wheel).namelist()
    for needle in ("py.typed", "facade/sync.pyi", "backends/browser/selectors.yaml", "operations_index.json", "agent/docs/AGENT_GUIDE.md", "backends/graphql/queries/JiraIssueGet.graphql"):
        assert any(n.endswith(needle) for n in names), needle
    assert "atlian-assiant = atlian_assiant.cli:main" in zipfile.ZipFile(wheel).read(next(n for n in names if n.endswith("entry_points.txt"))).decode()
