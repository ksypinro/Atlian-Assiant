"""The MCP backend speaks the LIVE Rovo MCP v2 surface recorded in backends/mcp/catalog_v2.json (VER-08 evidence).

The real server silently ignores an unknown argument name, so a renamed input would quietly drop a filter. These tests pin every literal call site
to the recorded catalogue; FakeMcpTransport additionally rejects unknown inputs at run time for call sites that build their arguments dynamically.
"""
from __future__ import annotations

import ast
from pathlib import Path

import atlian_assiant.backends.mcp as mcp_pkg
from atlian_assiant.backends.mcp.backend import ALL_OP_TOOLS, CATALOG, EXECUTE_TOOLS, route

SOURCES = [Path(mcp_pkg.__file__).parent / "backend.py", Path(mcp_pkg.__file__).parent / "write.py"]
ACCEPTED = {**CATALOG["direct"], **{name: entry["inputs"] for name, entry in CATALOG["execute"].items()}}


def _literal_calls():
    for src in SOURCES:
        for node in ast.walk(ast.parse(src.read_text())):
            if (isinstance(node, ast.Call) and getattr(node.func, "attr", "") in ("raw", "offset_page", "token_page") and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                keys = [k.value for k in node.args[1].keys if isinstance(k, ast.Constant)] if len(node.args) > 1 and isinstance(node.args[1], ast.Dict) else []
                yield f"{src.name}:{node.lineno}", node.args[0].value, keys


def test_every_literal_call_uses_input_names_the_live_server_accepts():
    bad = [f"{where} {tool}: {sorted(set(keys) - set(ACCEPTED[tool]) - {'cloudId'})}" for where, tool, keys in _literal_calls()
           if tool in ACCEPTED and set(keys) - set(ACCEPTED[tool]) - {"cloudId"}]
    assert not bad, "\n".join(bad)


def test_catalogue_routing_direct_tools_win_and_execute_family_is_used_otherwise():
    live = set(CATALOG["direct"])
    assert route("getJiraIssue", live) == "getJiraIssue"
    assert route("listJiraIssueComments", live) == "executeRead" and EXECUTE_TOOLS["manageJiraSprint"] == "executeDestructive"
    assert route("deleteJiraIssue", live) is None                                   # left the catalogue -> Unsupported -> REST
    assert route("listJiraIssueComments", live - {"executeRead"}) is None


def test_the_recorded_surface_has_the_shape_seen_live():
    assert {"discover", "executeRead", "executeWrite", "executeDestructive", "getJiraIssue", "searchJiraIssuesUsingJql"} <= set(CATALOG["direct"])
    assert len(CATALOG["execute"]) > 100
    reachable = {op for op, tool in ALL_OP_TOOLS.items() if route(tool, set(CATALOG["direct"]))}
    assert {"jira.issue.get", "jira.issue.comments.list", "jira.issue.transitions.list", "jira.project.list", "confluence.space.list"} <= reachable
