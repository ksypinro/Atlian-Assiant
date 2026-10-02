from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Access(StrEnum):
    """Permission class of an operation. Mirrors Atlassian MCP v2 permission groups (DA §9–11)."""

    READ = "read"            # read_jira / read_confluence / read:all:twg
    SEARCH = "search"        # search_jira / search_confluence / search_rovo
    WRITE = "write"          # write_jira / write_confluence / write:all:twg
    DELETE = "delete"        # delete_jira (admin must enable in MCP)
    MANAGE = "manage"        # manage_jira (projects, workflows, screens)
    ORG_ADMIN = "org_admin"  # organization audit / admin APIs
    ANALYTICS = "analytics"  # Data Lake: NOT row-level permissioned (DA §21)


class BackendKind(StrEnum):
    REST = "rest"            # Cloud REST: Jira v3, Agile, JSM, Confluence v2 + v1/CQL
    REST_DC = "rest_dc"      # Data Center / Server REST
    MCP = "mcp"              # https://mcp.atlassian.com/v2/mcp
    GRAPHQL = "graphql"      # Atlassian GraphQL Gateway
    ACLI = "acli"            # official Atlassian CLI (Jira)
    TWG = "twg"              # Teamwork Graph CLI
    JIRA_CLI = "jira_cli"    # community ankitpokhrel/jira-cli
    BROWSER = "browser"      # Playwright UI automation over a signed-in session
    DATA_LAKE = "data_lake"  # Delta Sharing data shares
    ORG_ADMIN = "org_admin"  # api.atlassian.com/admin
    FORGE = "forge"          # Forge App REST API / Forge Remote
    DATABASE = "database"    # DC direct DB: read-only, opt-in, discouraged


class Product(StrEnum):
    JIRA = "jira"
    JIRA_SOFTWARE = "jira_software"
    JSM = "jsm"
    CONFLUENCE = "confluence"
    PLATFORM = "platform"    # graph, org admin, analytics


class Deployment(StrEnum):
    CLOUD = "cloud"
    DATA_CENTER = "data_center"


@dataclass(frozen=True, slots=True)
class Operation:
    """Canonical, backend-independent identity of one task (e.g. "jira.issue.get")."""

    id: str
    product: Product
    access: Access
    summary: str
    idempotent: bool = True          # governs retry and fallback rules (§12.4)
    consumes_credits: bool = False   # Rovo / Teamwork Graph enrichment (DA §11)
    cloud_only: bool = False         # MCP, TWG, Data Lake, Org Admin, Forge
    tags: frozenset[str] = field(default_factory=frozenset)
