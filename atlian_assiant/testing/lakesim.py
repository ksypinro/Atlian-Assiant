"""Data Lake tables generated from the shared scenario (design 18): what a share would hold if it mirrored the scenario's Jira / JSM / Confluence data."""
from __future__ import annotations

from typing import Any

from ..backends.data_lake.share import InMemoryShareClient
from ..formats.adf import adf_to_markdown
from .scenario import scenario


def _md(v: Any) -> Any:
    return adf_to_markdown(v) if isinstance(v, dict) else v


def _u(prefix: str, u: dict[str, Any] | None) -> dict[str, Any]:
    u = u or {}
    return {f"{prefix}_id": u.get("accountId"), f"{prefix}_name": u.get("displayName"), f"{prefix}_email": u.get("emailAddress")} if u else {}


def lake_tables(raw: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    raw = raw or scenario()
    j, a, s, c = raw["jira"], raw["agile"], raw["jsm"], raw["confluence"]
    t: dict[str, list[dict[str, Any]]] = {}
    t["jira_issue"] = [{"issue_id": i["id"], "issue_key": i["key"], "project_key": i["fields"]["project"]["key"], "summary": i["fields"]["summary"],
                        "description": _md(i["fields"].get("description")), "status": i["fields"]["status"]["name"],
                        "status_category": i["fields"]["status"]["statusCategory"]["name"], "issue_type": i["fields"]["issuetype"]["name"],
                        "priority": (i["fields"].get("priority") or {}).get("name"), **_u("assignee", i["fields"].get("assignee")), **_u("reporter", i["fields"].get("reporter")),
                        "labels": ",".join(i["fields"].get("labels") or []), "created": i["fields"].get("created"), "updated": i["fields"].get("updated")} for i in j["issues"]]
    t["jira_comment"] = [{"comment_id": x["id"], "issue_key": k, **_u("author", x.get("author")), "body": _md(x.get("body")), "created": x.get("created"), "updated": x.get("updated")}
                         for k, v in j["comments"].items() for x in v]
    t["jira_changelog"] = [{"history_id": h["id"], "issue_key": k, **_u("author", h.get("author")), "created": h.get("created"), "field": it["field"],
                            "from_string": it.get("fromString"), "to_string": it.get("toString")} for k, v in j["changelog"].items() for h in v for it in h["items"]]
    t["jira_worklog"] = [{"worklog_id": w["id"], "issue_key": k, **_u("author", w.get("author")), "time_spent": w.get("timeSpent"), "time_spent_seconds": w.get("timeSpentSeconds"),
                          "started": w.get("started")} for k, v in j["worklogs"].items() for w in v]
    t["jira_project"] = [{"project_id": p["id"], "project_key": p["key"], "name": p["name"], "project_type": p.get("projectTypeKey"),
                          "lead_id": (p.get("lead") or {}).get("accountId"), "lead_name": (p.get("lead") or {}).get("displayName")} for p in j["projects"]]
    t["jira_component"] = [{"component_id": x["id"], "project_key": k, "name": x["name"], "lead_name": (x.get("lead") or {}).get("displayName")} for k, v in j["components"].items() for x in v]
    t["jira_version"] = [{"version_id": x["id"], "project_key": k, "name": x["name"], "released": x.get("released"), "release_date": x.get("releaseDate")}
                         for k, v in j["versions"].items() for x in v]
    t["jira_user"] = [{"account_id": u["accountId"], "display_name": u["displayName"], "email": u.get("emailAddress"), "active": u.get("active", True)} for u in j["users"]]
    t["jira_field"] = [{"field_id": f["id"], "name": f["name"], "is_custom": f.get("custom"), "schema_type": (f.get("schema") or {}).get("type")} for f in j["fields"]]
    t["jira_issue_type"] = [{"issue_type_id": x["id"], "name": x["name"], "is_subtask": x.get("subtask")} for x in j["issue_types"]]
    t["jira_status"] = [{"status_id": x["id"], "name": x["name"], "category": x["statusCategory"]["name"]} for x in j["statuses"]]
    t["jira_priority"] = [{"name": x["name"]} for x in j["priorities"]]
    t["jira_link_type"] = [{"name": x["name"]} for x in j["link_types"]]
    t["jira_board"] = [{"board_id": b["id"], "name": b["name"], "board_type": b.get("type"), "project_key": (b.get("location") or {}).get("projectKey")} for b in a["boards"]]
    t["jira_sprint"] = [{"sprint_id": x["id"], "board_id": k, "name": x["name"], "state": x.get("state"), "goal": x.get("goal"), "start_date": x.get("startDate"),
                         "end_date": x.get("endDate")} for k, v in a["sprints"].items() for x in v]
    t["jira_sprint_issue"] = [{"sprint_id": k, "issue_key": i["key"]} for k, v in a["sprint_issues"].items() for i in v]
    t["jira_epic"] = [{"epic_id": e["id"], "board_id": k, "epic_key": e["key"], "name": e.get("name"), "is_done": e.get("done")} for k, v in a["epics"].items() for e in v]
    t["jsm_info"] = [{"version": s["info"]["version"], "platform_version": s["info"].get("platformVersion"), "licensed": s["info"].get("isLicensedForUse")}]
    t["jsm_service_desk"] = [{"service_desk_id": d["id"], "project_id": d["projectId"], "project_key": d["projectKey"], "project_name": d["projectName"]} for d in s["service_desks"]]
    t["jsm_request_type"] = [{"request_type_id": r["id"], "service_desk_id": k, "name": r["name"], "description": r.get("description")} for k, v in s["request_types"].items() for r in v]
    t["jsm_request"] = [{"issue_id": r["issueId"], "issue_key": r["issueKey"], "request_type_id": r.get("requestTypeId"), "service_desk_id": r.get("serviceDeskId"),
                         "status": (r.get("currentStatus") or {}).get("status"), **_u("reporter", r.get("reporter")), "created": (r.get("createdDate") or {}).get("iso8601"),
                         "summary": next((f["value"] for f in r["requestFieldValues"] if f["fieldId"] == "summary"), None),
                         "description": next((f["value"] for f in r["requestFieldValues"] if f["fieldId"] == "description"), None)} for r in s["requests"]]
    t["jsm_request_comment"] = [{"comment_id": x["id"], "issue_key": k, **_u("author", x.get("author")), "body": x.get("body"), "is_public": x.get("public"),
                                 "created": (x.get("created") or {}).get("iso8601")} for k, v in s["comments"].items() for x in v]
    t["jsm_request_sla"] = [{"sla_id": x["id"], "issue_key": k, "name": x["name"], "breached": x["ongoingCycle"].get("breached"), "paused": x["ongoingCycle"].get("paused"),
                             "remaining_millis": x["ongoingCycle"]["remainingTime"]["millis"], "goal_millis": x["ongoingCycle"]["goalDuration"]["millis"]}
                            for k, v in s["sla"].items() for x in v]
    t["jsm_kb_article"] = [{"title": x["title"], "excerpt": x.get("excerpt"), "url": (x.get("content") or {}).get("iframeSrc")} for x in s["kb"]]
    t["jsm_organization"] = [{"organization_id": o["id"], "name": o.get("name")} for o in s["organizations"]]
    t["jsm_customer"] = [{"service_desk_id": k, "account_id": u["accountId"], "display_name": u.get("displayName"), "email": u.get("emailAddress")} for k, v in s["customers"].items() for u in v]
    space_key = {sp["id"]: sp["key"] for sp in c["spaces"]}
    t["confluence_page"] = [{"page_id": p["id"], "space_id": p["spaceId"], "space_key": space_key.get(p["spaceId"]), "title": p["title"], "status": p["status"], "parent_id": p.get("parentId"),
                             "version": p["version"]["number"], "body_storage": p["body"]["storage"]["value"], "created": p.get("createdAt"), "updated": p["version"].get("createdAt"),
                             "author_id": p.get("authorId"), "web_url": p["_links"]["webui"]} for p in c["pages"].values()]
    t["confluence_space"] = [{"space_id": sp["id"], "space_key": sp["key"], "name": sp.get("name"), "space_type": sp.get("type"), "status": sp.get("status"),
                              "homepage_id": sp.get("homepageId")} for sp in c["spaces"]]
    return t


def fake_share(raw: dict[str, Any] | None = None) -> InMemoryShareClient:
    return InMemoryShareClient(lake_tables(raw))
