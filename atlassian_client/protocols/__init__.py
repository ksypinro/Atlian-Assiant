"""Protocol contracts (design section 9). Backends satisfy them structurally."""

from .admin import OrgAuditReader
from .agile import JiraAgileReader, JiraAgileWriter
from .analytics import AnalyticsReader
from .automation import AutomationTrigger
from .base import Backend, BackendHealth, implementation_of
from .confluence import (
    ConfluenceContentDeleter,
    ConfluenceContentReader,
    ConfluenceContentWriter,
    ConfluencePermissionWriter,
    ConfluenceSearcher,
    ConfluenceSpaceReader,
    ConfluenceSpaceWriter,
    ConfluenceTemplateReader,
    ContentType,
)
from .exports import ConfluenceExporter, JiraExporter
from .graph import GraphQLExecutor, SemanticSearcher, TeamworkGraphReader, TeamworkGraphWriter
from .identity import IdentityReader
from .jira import (
    JiraAdmin,
    JiraIssueDeleter,
    JiraIssueReader,
    JiraIssueSearcher,
    JiraIssueWriter,
    JiraJqlTools,
    JiraMetadataReader,
    JiraProjectReader,
    JiraProjectWriter,
    JiraUserDirectory,
)
from .jsm import JsmReader, JsmWriter
from .raw import RawRequester
from .webhooks import WebhookManager

__all__ = ['AnalyticsReader', 'AutomationTrigger', 'Backend', 'BackendHealth', 'ConfluenceContentDeleter', 'ConfluenceContentReader', 'ConfluenceContentWriter', 'ConfluenceExporter', 'ConfluencePermissionWriter', 'ConfluenceSearcher', 'ConfluenceSpaceReader', 'ConfluenceSpaceWriter', 'ConfluenceTemplateReader', 'ContentType', 'GraphQLExecutor', 'IdentityReader', 'JiraAdmin', 'JiraAgileReader', 'JiraAgileWriter', 'JiraExporter', 'JiraIssueDeleter', 'JiraIssueReader', 'JiraIssueSearcher', 'JiraIssueWriter', 'JiraJqlTools', 'JiraMetadataReader', 'JiraProjectReader', 'JiraProjectWriter', 'JiraUserDirectory', 'JsmReader', 'JsmWriter', 'OrgAuditReader', 'RawRequester', 'SemanticSearcher', 'TeamworkGraphReader', 'TeamworkGraphWriter', 'WebhookManager', 'implementation_of']
