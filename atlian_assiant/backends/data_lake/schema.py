"""Data Lake table and column names (design 10.9). UNVERIFIED against a live share: tracked by VER-10 (Data Lake table names and schemas).

Everything that depends on a table or column name lives here and in ``ops.py`` so a verified schema is a one-file change (override with ``TableMap``).
"""
from __future__ import annotations

from dataclasses import dataclass, field

GOVERNANCE_NOTICE = ("Data Lake shares are NOT row-level permissioned and do not mirror product permissions (DA 21, 26): treat this as organization-wide data and do not "
                     "show it to people who lack access to the underlying Jira / Confluence content.")

TABLES: dict[str, tuple[str, ...]] = {
    "jira_issue": ("issue_id", "issue_key", "project_key", "summary", "description", "status", "status_category", "issue_type", "priority", "assignee_id", "assignee_name",
                   "assignee_email", "reporter_id", "reporter_name", "labels", "created", "updated"),
    "jira_comment": ("comment_id", "issue_key", "author_id", "author_name", "body", "created", "updated"),
    "jira_changelog": ("history_id", "issue_key", "author_id", "author_name", "created", "field", "from_string", "to_string"),
    "jira_worklog": ("worklog_id", "issue_key", "author_id", "author_name", "time_spent", "time_spent_seconds", "started"),
    "jira_project": ("project_id", "project_key", "name", "project_type", "lead_id", "lead_name"),
    "jira_component": ("component_id", "project_key", "name", "lead_name"),
    "jira_version": ("version_id", "project_key", "name", "released", "release_date"),
    "jira_user": ("account_id", "display_name", "email", "active"),
    "jira_field": ("field_id", "name", "is_custom", "schema_type"),
    "jira_issue_type": ("issue_type_id", "name", "is_subtask"),
    "jira_status": ("status_id", "name", "category"),
    "jira_priority": ("name",),
    "jira_link_type": ("name",),
    "jira_board": ("board_id", "name", "board_type", "project_key"),
    "jira_sprint": ("sprint_id", "board_id", "name", "state", "goal", "start_date", "end_date"),
    "jira_sprint_issue": ("sprint_id", "issue_key"),
    "jira_epic": ("epic_id", "board_id", "epic_key", "name", "is_done"),
    "jsm_info": ("version", "platform_version", "licensed"),
    "jsm_service_desk": ("service_desk_id", "project_id", "project_key", "project_name"),
    "jsm_request_type": ("request_type_id", "service_desk_id", "name", "description"),
    "jsm_request": ("issue_id", "issue_key", "request_type_id", "service_desk_id", "status", "reporter_id", "reporter_name", "reporter_email", "created", "summary", "description"),
    "jsm_request_comment": ("comment_id", "issue_key", "author_id", "author_name", "body", "is_public", "created"),
    "jsm_request_sla": ("sla_id", "issue_key", "name", "breached", "paused", "remaining_millis", "goal_millis"),
    "jsm_kb_article": ("title", "excerpt", "url"),
    "jsm_organization": ("organization_id", "name"),
    "jsm_customer": ("service_desk_id", "account_id", "display_name", "email"),
    "confluence_page": ("page_id", "space_id", "space_key", "title", "status", "parent_id", "version", "body_storage", "created", "updated", "author_id", "web_url"),
    "confluence_space": ("space_id", "space_key", "name", "space_type", "status", "homepage_id"),
}


@dataclass(frozen=True)
class TableMap:
    """Maps logical table names to the share's real ``share.schema.table`` names (identity by default)."""

    names: dict[str, str] = field(default_factory=dict)

    def physical(self, logical: str) -> str:
        return self.names.get(logical, logical)
