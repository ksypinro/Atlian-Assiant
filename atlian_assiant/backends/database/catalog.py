"""The curated query catalog of the DATABASE backend (design 10.12), tied to a Jira Data Center product version. There is NO free-form SQL: callers name a query
and pass parameters, and every statement below is read-only, parameterized (``:name``) and reviewed against the schema of the listed versions.

The schema (``jiraissue``, ``project``, ``issuestatus``) is Jira DC's; each entry lists the product-version prefixes it was written for. Direct DB access is
discouraged (DA 24): prefer REST."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CuratedQuery:
    name: str
    sql: str
    params: tuple[str, ...] = ()
    description: str = ""


_COUNT = ("SELECT COUNT(*) AS n FROM jiraissue i JOIN project p ON p.id = i.PROJECT WHERE (:project IS NULL OR p.pkey = :project) "
          "AND (:status IS NULL OR i.issuestatus = (SELECT s.id FROM issuestatus s WHERE s.pname = :status))")

JIRA_DC_QUERIES: dict[str, CuratedQuery] = {q.name: q for q in (
    CuratedQuery("issue_count", _COUNT, ("project", "status"), "Number of issues, optionally for one project and / or one status name."),
    CuratedQuery("issue_counts_by_status", "SELECT s.pname AS status, COUNT(*) AS n FROM jiraissue i JOIN issuestatus s ON s.id = i.issuestatus GROUP BY s.pname ORDER BY s.pname",
                 (), "Issue counts per status name."),
    CuratedQuery("issue_counts_by_project", "SELECT p.pkey AS project, COUNT(*) AS n FROM jiraissue i JOIN project p ON p.id = i.PROJECT GROUP BY p.pkey ORDER BY p.pkey",
                 (), "Issue counts per project key."),
)}

#: product-version prefix -> catalog. A version with no entry is refused: nobody reviewed these statements against its schema.
CATALOGS: dict[str, dict[str, CuratedQuery]] = {"9.": JIRA_DC_QUERIES, "10.": JIRA_DC_QUERIES}


def catalog_for(version: str) -> dict[str, CuratedQuery] | None:
    for prefix, cat in CATALOGS.items():
        if version.startswith(prefix):
            return cat
    return None
