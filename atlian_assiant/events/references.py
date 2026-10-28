"""Official documentation registry (EV section 62, CC-15). Hints, ``describe()`` and the docs link to these."""
from __future__ import annotations

REFERENCES: dict[str, tuple[str, ...]] = {
    "jira_webhooks": ("https://developer.atlassian.com/cloud/jira/platform/webhooks/",
                      "https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-webhooks/"),
    "forge_events": ("https://developer.atlassian.com/platform/forge/events-reference/",
                     "https://developer.atlassian.com/platform/forge/manifest-reference/modules/trigger/",
                     "https://developer.atlassian.com/platform/forge/events-reference/jira/",
                     "https://developer.atlassian.com/platform/forge/events-reference/jira-software/",
                     "https://developer.atlassian.com/platform/forge/events-reference/jira-service-management/",
                     "https://developer.atlassian.com/platform/forge/events-reference/confluence/"),
    "forge_dynamic_modules": ("https://developer.atlassian.com/platform/forge/apis-reference/dynamic-modules/",
                              "https://developer.atlassian.com/platform/forge/apis-reference/dynamic-modules-api/"),
    "forge_async_events": ("https://developer.atlassian.com/platform/forge/runtime-reference/async-events-api/",),
    "forge_realtime": ("https://developer.atlassian.com/platform/forge/realtime/",
                       "https://developer.atlassian.com/platform/forge/runtime-reference/realtime-events-api/",
                       "https://developer.atlassian.com/platform/forge/apis-reference/ui-api-bridge/realtime/"),
    "forge_app_rest": ("https://developer.atlassian.com/platform/forge/app-rest-apis/",
                       "https://developer.atlassian.com/platform/forge/expose-forge-app-rest-apis/",
                       "https://developer.atlassian.com/platform/forge/access-rest-apis-exposed-by-a-forge-app/"),
    "org_audit": ("https://developer.atlassian.com/cloud/admin/organization/rest/api-group-events/",
                  "https://developer.atlassian.com/cloud/admin/organization/poll-events-migration/",
                  "https://support.atlassian.com/security-and-access-policies/docs/learn-more-about-audit-log-webhooks/",
                  "https://support.atlassian.com/security-and-access-policies/docs/manage-event-exclusions-from-your-audit-log/"),
    "automation": ("https://support.atlassian.com/cloud-automation/docs/jira-automation-actions/",
                   "https://support.atlassian.com/cloud-automation/docs/what-third-party-applications-can-i-integrate-with/",
                   "https://support.atlassian.com/cloud-automation/docs/actions-in-confluence-automation/"),
}

#: delivery method -> registry keys relevant to it (used by hints and the generated docs)
BY_METHOD: dict[str, tuple[str, ...]] = {
    "DM-01": ("jira_webhooks",), "DM-02": ("jira_webhooks",), "DM-03": ("jira_webhooks",), "DM-05": ("jira_webhooks",),
    "DM-15": ("automation",), "DM-16": ("automation",), "DM-36": ("jira_webhooks",), "DM-37": ("automation",),
}


def links_for(method_id: str) -> tuple[str, ...]:
    """Official documentation URLs for a delivery method (empty when none is registered)."""
    return tuple(url for key in BY_METHOD.get(method_id, ()) for url in REFERENCES[key])
