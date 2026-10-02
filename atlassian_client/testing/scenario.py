"""A small, realistic Atlassian dataset in raw API shapes.

It feeds BOTH the in-memory FakeBackend (normalized through the same functions the REST backend uses) and the
ScenarioServer used to mock HTTP in tests, so one contract suite can assert identical behaviour on every backend.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

ADF_DESC = {"version": 1, "type": "doc", "content": [
    {"type": "paragraph", "content": [{"type": "text", "text": "Login fails with "}, {"type": "text", "text": "500", "marks": [{"type": "strong"}]}]}]}


def _user(account: str, name: str, email: str | None = None) -> dict[str, Any]:
    return {"accountId": account, "displayName": name, "emailAddress": email, "active": True}


ANN = _user("acc-ann", "Ann Lee", "ann@example.com")
BOB = _user("acc-bob", "Bob Ray", "bob@example.com")


def _issue(n: int, summary: str, status: str, assignee: dict[str, Any] | None) -> dict[str, Any]:
    return {"id": str(10000 + n), "key": f"PROJ-{n}", "fields": {
        "summary": summary, "status": {"id": str(n), "name": status, "statusCategory": {"name": "In Progress" if status != "Done" else "Done"}},
        "issuetype": {"name": "Bug"}, "project": {"id": "1", "key": "PROJ", "name": "Project"}, "priority": {"name": "High"},
        "assignee": assignee, "reporter": BOB, "labels": ["backend"] if n == 1 else [], "created": "2026-09-01T10:00:00.000+0000",
        "updated": f"2026-09-0{n}T12:00:00.000+0000", "description": ADF_DESC if n == 1 else None,
        "attachment": [], "issuelinks": [], "customfield_10016": 3}}


ISSUES = [_issue(1, "Login fails", "In Progress", ANN), _issue(2, "Slow dashboard", "To Do", None), _issue(3, "Typo on homepage", "Done", BOB)]
ISSUES[0]["fields"]["attachment"] = [{"id": "att1", "filename": "log.txt", "size": 5, "mimeType": "text/plain", "author": ANN,
                                      "created": "2026-09-01T10:05:00.000+0000", "content": "https://x/att1"}]
ISSUES[0]["fields"]["issuelinks"] = [{"id": "l1", "type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
                                      "outwardIssue": {"key": "PROJ-2", "fields": {"summary": "Slow dashboard"}}}]

RAW: dict[str, Any] = {
    "jira": {
        "myself": ANN,
        "users": [ANN, BOB],
        "issues": ISSUES,
        "comments": {"PROJ-1": [{"id": "c1", "author": BOB, "body": ADF_DESC, "created": "2026-09-01T11:00:00.000+0000", "updated": "2026-09-01T11:00:00.000+0000"},
                                {"id": "c2", "author": ANN, "body": ADF_DESC, "created": "2026-09-01T12:00:00.000+0000", "updated": "2026-09-01T12:00:00.000+0000"},
                                {"id": "c3", "author": ANN, "body": ADF_DESC, "created": "2026-09-01T13:00:00.000+0000", "updated": "2026-09-01T13:00:00.000+0000"}]},
        "changelog": {"PROJ-1": [{"id": "h1", "author": ANN, "created": "2026-09-01T10:30:00.000+0000",
                                  "items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}]}]},
        "worklogs": {"PROJ-1": [{"id": "w1", "author": ANN, "timeSpent": "2h", "timeSpentSeconds": 7200, "started": "2026-09-01T09:00:00.000+0000"}]},
        "transitions": {"PROJ-1": [{"id": "31", "name": "Done", "to": {"id": "3", "name": "Done", "statusCategory": {"name": "Done"}}}]},
        "remote_links": {"PROJ-1": [{"id": 77, "object": {"url": "https://example.com/doc", "title": "Design doc"}}]},
        "watchers": {"PROJ-1": [ANN, BOB]},
        "properties": {"PROJ-1": {"my.app.state": {"key": "my.app.state", "value": {"seen": True}}}},
        "attachment_content": {"att1": b"hello"},
        "projects": [{"id": "1", "key": "PROJ", "name": "Project", "projectTypeKey": "software", "lead": ANN},
                     {"id": "2", "key": "OPS", "name": "Operations", "projectTypeKey": "service_desk", "lead": BOB}],
        "components": {"PROJ": [{"id": "c10", "name": "API", "lead": ANN}]},
        "versions": {"PROJ": [{"id": "v1", "name": "1.0", "released": True, "releaseDate": "2026-08-01"}]},
        "project_statuses": {"PROJ": [{"id": "10", "name": "Bug", "statuses": [{"id": "1", "name": "To Do", "statusCategory": {"name": "To Do"}},
                                                                             {"id": "3", "name": "Done", "statusCategory": {"name": "Done"}}]}]},
        "fields": [{"id": "summary", "name": "Summary", "custom": False, "schema": {"type": "string"}},
                   {"id": "customfield_10016", "name": "Story Points", "custom": True, "schema": {"type": "number"}}],
        "issue_types": [{"id": "10", "name": "Bug", "subtask": False}, {"id": "11", "name": "Task", "subtask": False}],
        "statuses": [{"id": "1", "name": "To Do", "statusCategory": {"name": "To Do"}}, {"id": "3", "name": "Done", "statusCategory": {"name": "Done"}}],
        "priorities": [{"name": "High"}, {"name": "Low"}],
        "link_types": [{"name": "Blocks"}, {"name": "Relates"}],
        "server_info": {"baseUrl": "https://example.atlassian.net", "version": "1001.0.0", "deploymentType": "Cloud", "serverTitle": "Example",
                        "serverTimeZone": "UTC"},
        "create_fields": {"PROJ/10": [{"fieldId": "summary", "name": "Summary", "required": True, "schema": {"type": "string"}},
                                      {"fieldId": "customfield_10016", "name": "Story Points", "required": False, "schema": {"type": "number"}}]},
        "filters": [{"id": "f1", "name": "My open issues", "jql": "assignee = currentUser() AND resolution = EMPTY", "owner": ANN}],
        "dashboards": [{"id": "d1", "name": "Team", "view": "https://example.atlassian.net/jira/dashboards/d1"}],
    },
    "agile": {
        "boards": [{"id": 42, "name": "PROJ board", "type": "scrum", "location": {"projectKey": "PROJ"}}],
        "configs": {"42": {"id": 42, "name": "PROJ board", "filter": {"id": "f1"}, "columnConfig": {"columns": [{"name": "To Do"}, {"name": "Done"}]}}},
        "board_issues": {"42": [ISSUES[0], ISSUES[1]]},
        "sprints": {"42": [{"id": 7, "name": "Sprint 7", "state": "active", "startDate": "2026-09-01T00:00:00.000Z", "goal": "Ship login fix"}]},
        "sprint_issues": {"7": [ISSUES[0]]},
        "backlog": {"42": [ISSUES[1]]},
        "epics": {"42": [{"id": 900, "key": "PROJ-900", "name": "Auth", "done": False}]},
    },
    "jsm": {
        "info": {"version": "1001.0.0", "platformVersion": "1001.0.0", "isLicensedForUse": True},
        "service_desks": [{"id": "4", "projectId": "2", "projectKey": "OPS", "projectName": "Operations"}],
        "request_types": {"4": [{"id": "r1", "name": "Get IT help", "description": "Ask IT", "serviceDeskId": "4"}]},
        "requests": [{"issueId": "20001", "issueKey": "OPS-1", "requestTypeId": "r1", "serviceDeskId": "4", "currentStatus": {"status": "Open"},
                      "reporter": BOB, "createdDate": {"iso8601": "2026-09-02T08:00:00+0000"},
                      "requestFieldValues": [{"fieldId": "summary", "value": "VPN down"}, {"fieldId": "description", "value": "Cannot connect"}]}],
        "comments": {"OPS-1": [{"id": "rc1", "author": ANN, "body": "Looking", "public": True, "created": {"iso8601": "2026-09-02T09:00:00+0000"}},
                               {"id": "rc2", "author": ANN, "body": "Internal note", "public": False, "created": {"iso8601": "2026-09-02T09:05:00+0000"}}]},
        "sla": {"OPS-1": [{"id": "s1", "name": "Time to resolution", "ongoingCycle": {"breached": False, "paused": False,
                                                                                     "remainingTime": {"millis": 3600000}, "goalDuration": {"millis": 7200000}}}]},
        "kb": [{"title": "Fix VPN", "excerpt": "Restart the client", "content": {"iframeSrc": "https://example.com/kb/vpn"}}],
        "organizations": [{"id": "o1", "name": "Acme"}],
        "customers": {"4": [BOB]},
    },
    "confluence": {
        "spaces": [{"id": "9001", "key": "ENG", "name": "Engineering", "type": "global", "status": "current", "homepageId": "123"},
                   {"id": "9002", "key": "OPS", "name": "Ops", "type": "global", "status": "current"}],
        "pages": {
            "123": {"id": "123", "status": "current", "title": "Runbook", "spaceId": "9001", "parentId": "100", "authorId": "acc-ann",
                    "createdAt": "2026-08-01T10:00:00.000Z", "version": {"number": 3, "createdAt": "2026-09-01T10:00:00.000Z", "message": "tweak"},
                    "body": {"storage": {"value": "<h1>Runbook</h1><p>Restart <strong>nginx</strong></p>"}},
                    "_links": {"webui": "/spaces/ENG/pages/123/Runbook"}},
            "124": {"id": "124", "status": "current", "title": "Child page", "spaceId": "9001", "parentId": "123", "authorId": "acc-bob",
                    "createdAt": "2026-08-02T10:00:00.000Z", "version": {"number": 1, "createdAt": "2026-08-02T10:00:00.000Z"},
                    "body": {"storage": {"value": "<p>Child</p>"}}, "_links": {"webui": "/spaces/ENG/pages/124/Child"}},
            "125": {"id": "125", "status": "current", "title": "Another child", "spaceId": "9001", "parentId": "123", "authorId": "acc-bob",
                    "createdAt": "2026-08-03T10:00:00.000Z", "version": {"number": 1, "createdAt": "2026-08-03T10:00:00.000Z"},
                    "body": {"storage": {"value": "<p>Another</p>"}}, "_links": {"webui": "/spaces/ENG/pages/125/Another"}},
            "100": {"id": "100", "status": "current", "title": "Home", "spaceId": "9001", "version": {"number": 1, "createdAt": "2026-07-01T10:00:00.000Z"},
                    "body": {"storage": {"value": "<p>Home</p>"}}, "_links": {"webui": "/spaces/ENG/overview"}},
        },
        "page_versions": {"123": {"2": "<h1>Runbook</h1><p>Restart nginx</p>", "3": "<h1>Runbook</h1><p>Restart <strong>nginx</strong></p>"}},
        "children": {"123": ["124", "125"]},
        "ancestors": {"123": ["100"]},
        "descendants": {"123": ["124", "125"]},
        "versions": {"123": [{"number": 3, "message": "tweak", "authorId": "acc-ann", "createdAt": "2026-09-01T10:00:00.000Z"},
                             {"number": 2, "message": "first", "authorId": "acc-ann", "createdAt": "2026-08-15T10:00:00.000Z"}]},
        "labels": {"123": [{"id": "1", "name": "runbook", "prefix": "global"}]},
        "properties": {"123": [{"key": "app.state", "value": {"ok": True}}]},
        "footer_comments": {"123": [{"id": "fc1", "body": {"storage": {"value": "<p>Thanks</p>"}}, "version": {"authorId": "acc-bob", "createdAt": "2026-09-02T10:00:00.000Z"}}]},
        "inline_comments": {"123": [{"id": "ic1", "body": {"storage": {"value": "<p>Typo here</p>"}}, "resolutionStatus": "open",
                                     "version": {"authorId": "acc-bob", "createdAt": "2026-09-02T11:00:00.000Z"}}]},
        "attachments": {"123": [{"id": "cat1", "title": "diagram.png", "mediaType": "image/png", "fileSize": 4, "pageId": "123",
                                 "downloadLink": "/download/attachments/123/diagram.png", "version": {"number": 1}},
                                {"id": "cat2", "title": "notes.txt", "mediaType": "text/plain", "fileSize": 5, "pageId": "123",
                                 "downloadLink": "/download/attachments/123/notes.txt", "version": {"number": 1}}]},
        "attachment_content": {"cat1": b"\x89PNG", "cat2": b"notes"},
        "operations": {"123": [{"operation": "read", "targetType": "page"}, {"operation": "update", "targetType": "page"}]},
        "restrictions": {"123": {"read": {"operation": "read", "restrictions": {"user": {"results": [{"accountId": "acc-ann", "displayName": "Ann Lee"}]},
                                                                                "group": {"results": [{"name": "eng"}]}}},
                                 "update": {"operation": "update", "restrictions": {"user": {"results": []}, "group": {"results": []}}}}},
        "tasks": [{"id": "t1", "status": "incomplete", "pageId": "123", "body": {"storage": {"value": "<p>Rotate keys</p>"}}},
                  {"id": "t2", "status": "complete", "pageId": "123", "body": {"storage": {"value": "<p>Write docs</p>"}}}],
        "templates": [{"templateId": "tpl1", "name": "Runbook template", "description": "Standard", "space": {"key": "ENG"}}],
        "search": [{"content": {"id": "123", "type": "page", "title": "Runbook"}, "title": "Runbook", "excerpt": "Restart nginx",
                    "url": "/spaces/ENG/pages/123", "lastModified": "2026-09-01T10:00:00.000Z", "resultGlobalContainer": {"title": "Engineering"}},
                   {"content": {"id": "124", "type": "page", "title": "Child page"}, "title": "Child page", "excerpt": "Child",
                    "url": "/spaces/ENG/pages/124", "lastModified": "2026-08-02T10:00:00.000Z", "resultGlobalContainer": {"title": "Engineering"}},
                   {"content": {"id": "125", "type": "page", "title": "Another child"}, "title": "Another child", "excerpt": "Another",
                    "url": "/spaces/ENG/pages/125", "lastModified": "2026-08-03T10:00:00.000Z", "resultGlobalContainer": {"title": "Engineering"}}],
    },
}


def scenario() -> dict[str, Any]:
    """A fresh deep copy so tests may mutate it."""
    return deepcopy(RAW)
