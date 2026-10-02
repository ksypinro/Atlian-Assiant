from atlassian_client import operations as ops
from atlassian_client.backends.rest_cloud import SUPPORTED
from atlassian_client.capabilities import Access, Product


def test_catalog_ids_are_unique_dotted_and_consistent():
    assert len(ops.CATALOG) > 150
    for op_id, op in ops.CATALOG.items():
        assert op.id == op_id and op_id.count(".") >= 1 and op_id == op_id.lower()


def test_every_namespace_of_the_design_exists():
    prefixes = {"jira.issue", "jira.project", "jira.meta", "jira.user", "jira.filter", "jira.dashboard", "jira.jql", "jira.expression",
                "jira.admin", "jira.agile", "jsm", "confluence.content", "confluence.comment", "confluence.attachment", "confluence.label",
                "confluence.property", "confluence.task", "confluence.reaction", "confluence.watch", "confluence.star", "confluence.permission",
                "confluence.space", "confluence.search", "confluence.template", "confluence.special", "graph", "analytics", "export",
                "automation", "admin.audit", "webhook", "identity", "raw"}
    have = {o.rsplit(".", 1)[0] for o in ops.CATALOG}
    for p in prefixes:
        assert any(h == p or h.startswith(p + ".") for h in have), p


def test_access_classes_and_flags():
    assert ops.JIRA_ISSUE_GET.access is Access.READ and ops.JIRA_ISSUE_SEARCH.access is Access.SEARCH
    assert ops.JIRA_ISSUE_CREATE.access is Access.WRITE and not ops.JIRA_ISSUE_CREATE.idempotent
    assert ops.JIRA_ISSUE_DELETE.access is Access.DELETE and ops.JIRA_PROJECT_CREATE.access is Access.MANAGE
    assert ops.GRAPH_SEMANTIC_SEARCH.consumes_credits and ops.GRAPH_CONTEXT.cloud_only
    assert ops.ANALYTICS_LOAD_TABLE.access is Access.ANALYTICS and ops.ORG_EVENTS_STREAM.access is Access.ORG_ADMIN
    assert {o.product for o in ops.by_prefix("jsm.")} == {Product.JSM}


def test_rest_supported_operations_are_all_in_the_catalog():
    from atlassian_client.backends.rest_cloud import READ_SUPPORTED, WRITE_SUPPORTED

    assert SUPPORTED <= set(ops.CATALOG), sorted(SUPPORTED - set(ops.CATALOG))
    assert all(ops.CATALOG[o].access in (Access.READ, Access.SEARCH) for o in READ_SUPPORTED)
    assert all(ops.CATALOG[o].access in (Access.WRITE, Access.DELETE, Access.MANAGE, Access.READ) for o in WRITE_SUPPORTED)


def test_get_unknown_lists_similar_ids():
    import pytest

    with pytest.raises(KeyError) as exc:
        ops.get("jira.issue.gett")
    assert "jira.issue" in str(exc.value)
