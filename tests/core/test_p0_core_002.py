import dataclasses

import pytest

from atlian_assiant.capabilities import Access, BackendKind, Deployment, Operation, Product


def test_enum_values_match_design():
    assert {a.value for a in Access} == {"read", "search", "write", "delete", "manage", "org_admin", "analytics"}
    assert {b.value for b in BackendKind} == {"rest", "rest_dc", "mcp", "graphql", "acli", "twg", "jira_cli", "browser", "data_lake",
                                              "org_admin", "forge", "database"}
    assert {d.value for d in Deployment} == {"cloud", "data_center"}
    assert {p.value for p in Product} == {"jira", "jira_software", "jsm", "confluence", "platform"}


def test_operation_is_frozen_hashable_with_safe_defaults():
    op = Operation("jira.issue.get", Product.JIRA, Access.READ, "Fetch")
    assert op.idempotent and not op.consumes_credits and not op.cloud_only and op.tags == frozenset()
    with pytest.raises(dataclasses.FrozenInstanceError):
        op.id = "x"  # type: ignore[misc]
    assert {op: 1}[op] == 1
