"""Jira / Confluence Data Center REST backend (design section 10.2)."""
from .backend import PARTIAL_OPS, ROW_OVERRIDES, RestDcBackend, RestDcBackendFactory, supported

__all__ = ["PARTIAL_OPS", "ROW_OVERRIDES", "RestDcBackend", "RestDcBackendFactory", "supported"]
