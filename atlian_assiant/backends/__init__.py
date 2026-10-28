"""Backends: adapters that implement protocol subsets (design section 10)."""
from __future__ import annotations

from ..factory import BackendFactory


def builtin_factories() -> list[BackendFactory]:
    from .acli import AcliBackendFactory
    from .browser import BrowserBackendFactory
    from .data_lake import DataLakeBackendFactory
    from .database import DatabaseBackendFactory
    from .forge import ForgeBackendFactory
    from .graphql import GraphQLBackendFactory
    from .jira_cli import JiraCliBackendFactory
    from .mcp import McpBackendFactory
    from .org_admin import OrgAdminBackendFactory
    from .rest_cloud import RestBackendFactory
    from .rest_dc import RestDcBackendFactory
    from .twg import TwgBackendFactory

    return [RestBackendFactory(), McpBackendFactory(), OrgAdminBackendFactory(), ForgeBackendFactory(), RestDcBackendFactory(), AcliBackendFactory(), TwgBackendFactory(), JiraCliBackendFactory(), GraphQLBackendFactory(), BrowserBackendFactory(), DataLakeBackendFactory(), DatabaseBackendFactory()]
