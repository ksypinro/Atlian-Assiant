"""AtlassianClient: one documented facade over every way of reading, writing and watching Jira and Confluence.

Quick start::

    from atlassian_client import AtlassianClient

    with AtlassianClient.from_env() as atl:        # ATLASSIAN_SITE, ATLASSIAN_EMAIL, ATLASSIAN_API_TOKEN
        print(atl.describe())                      # what is configured and why
        issue = atl.jira.issues.get("PROJ-123")

Async code uses ``AsyncAtlassianClient`` with ``await``. Agents start with ``AtlassianClient.for_agent()``.
"""
from ._version import __version__
from .capabilities import Access, BackendKind, Deployment, Operation, Product
from .config import AtlassianConfig
from .facade.client import AsyncAtlassianClient
from .facade.sync import AtlassianClient
from .factory import ClientFactory
from .routing import Preset

__all__ = ["Access", "AsyncAtlassianClient", "AtlassianClient", "AtlassianConfig", "BackendKind", "ClientFactory", "Deployment",
           "Operation", "Preset", "Product", "__version__"]
