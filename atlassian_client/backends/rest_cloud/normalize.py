"""Raw Atlassian JSON -> normalized models. Pure functions; no I/O."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ...formats.adf import adf_to_markdown
from ...formats.storage import storage_to_markdown
from ...models.common import UserRef
from ...models.confluence import Attachment as CAttachment
from ...models.confluence import (
    ConfluenceComment,
    Content,
    ContentVersion,
    Label,
    Restrictions,
    SearchHit,
    Space,
    Task,
    Template,
)
from ...models.jira import (
    Attachment,
    Board,
    BoardConfig,
    ChangelogEntry,
    ChangelogItem,
    Comment,
    Component,
    Dashboard,
    Epic,
    Field,
    Filter,
    Issue,
    IssueLink,
    IssueType,
    Project,
    ProjectRef,
    RemoteLink,
    ServerInfo,
    Sprint,
    Status,
    Transition,
    Version,
    Worklog,
)
from ...models.jsm import (
    CustomerRequest,
    JsmInfo,
    KbArticle,
    Organization,
    RequestComment,
    RequestType,
    ServiceDesk,
    SlaInfo,
)


def dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        text = value.replace("Z", "+00:00")
        # Jira uses +0000 (no colon)
        if len(text) > 5 and text[-5] in "+-" and text[-3] != ":":
            text = text[:-2] + ":" + text[-2:]
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def user(d: dict[str, Any] | None) -> UserRef | None:
    if not d:
        return None
    return UserRef(account_id=d.get("accountId"), username=d.get("name") or d.get("key"),
                   display_name=d.get("displayName"), email=d.get("emailAddress"), active=d.get("active"))


def rich(value: Any) -> str | None:
    """ADF dict -> Markdown; plain strings (DC wiki markup, JSM) pass through."""
    if value is None:
        return None
    if isinstance(value, dict):
        return adf_to_markdown(value)
    return str(value)


def status(d: dict[str, Any] | None) -> Status | None:
    if not d:
        return None
    return Status(id=str(d.get("id", "")), name=str(d.get("name", "")),
                  category=(d.get("statusCategory") or {}).get("name"))


def issue(d: dict[str, Any], base_url: str | None = None) -> Issue:
    f = d.get("fields") or {}
    proj = f.get("project")
    names = d.get("names") or {}
    desc = f.get("description")
    return Issue(
        id=str(d["id"]), key=d["key"], url=f"{base_url}/browse/{d['key']}" if base_url else None,
        summary=f.get("summary"), status=status(f.get("status")),
        issue_type=(f.get("issuetype") or {}).get("name"),
        project=ProjectRef(id=str(proj["id"]), key=proj["key"], name=proj.get("name")) if proj else None,
        priority=(f.get("priority") or {}).get("name"), assignee=user(f.get("assignee")), reporter=user(f.get("reporter")),
        labels=list(f.get("labels") or []), parent_key=(f.get("parent") or {}).get("key"),
        resolution=(f.get("resolution") or {}).get("name"), created=dt(f.get("created")), updated=dt(f.get("updated")),
        description_markdown=rich(desc), description_adf=desc if isinstance(desc, dict) else None,
        fields=f, field_names=dict(names), raw=d,
    )


def comment(d: dict[str, Any]) -> Comment:
    return Comment(id=str(d["id"]), author=user(d.get("author")), body_markdown=rich(d.get("body")),
                   created=dt(d.get("created")), updated=dt(d.get("updated")), visibility=d.get("visibility"), raw=d)


def changelog(d: dict[str, Any]) -> ChangelogEntry:
    return ChangelogEntry(id=str(d["id"]), author=user(d.get("author")), created=dt(d.get("created")), raw=d,
                          items=[ChangelogItem(field=i.get("field", ""), from_string=i.get("fromString"),
                                               to_string=i.get("toString")) for i in d.get("items", [])])


def worklog(d: dict[str, Any]) -> Worklog:
    return Worklog(id=str(d["id"]), author=user(d.get("author")), time_spent=d.get("timeSpent"),
                   time_spent_seconds=d.get("timeSpentSeconds"), started=dt(d.get("started")),
                   comment_markdown=rich(d.get("comment")), raw=d)


def transition(d: dict[str, Any]) -> Transition:
    return Transition(id=str(d["id"]), name=d.get("name", ""), to_status=status(d.get("to")), raw=d)


def attachment(d: dict[str, Any]) -> Attachment:
    return Attachment(id=str(d["id"]), filename=d.get("filename", ""), size=d.get("size"), mime_type=d.get("mimeType"),
                      author=user(d.get("author")), created=dt(d.get("created")), content_url=d.get("content"), raw=d)


def issue_link(d: dict[str, Any]) -> IssueLink:
    t = d.get("type") or {}
    if "outwardIssue" in d:
        direction, other, label = "outward", d["outwardIssue"], t.get("outward", t.get("name", ""))
    else:
        direction, other, label = "inward", d.get("inwardIssue", {}), t.get("inward", t.get("name", ""))
    return IssueLink(id=str(d.get("id", "")), link_type=label, direction=direction, issue_key=other.get("key", ""),
                     summary=(other.get("fields") or {}).get("summary"), raw=d)


def remote_link(d: dict[str, Any]) -> RemoteLink:
    o = d.get("object") or {}
    return RemoteLink(id=str(d["id"]) if "id" in d else None, url=o.get("url", ""), title=o.get("title"), raw=d)


def project(d: dict[str, Any]) -> Project:
    return Project(id=str(d["id"]), key=d["key"], name=d.get("name"), project_type=d.get("projectTypeKey"),
                   lead=user(d.get("lead")), url=d.get("self"), raw=d)


def jfield(d: dict[str, Any]) -> Field:
    return Field(id=d["id"], name=d.get("name", d["id"]), custom=bool(d.get("custom")),
                 schema_type=(d.get("schema") or {}).get("type"), required=d.get("required"), raw=d)


def issue_type(d: dict[str, Any]) -> IssueType:
    return IssueType(id=str(d["id"]), name=d.get("name", ""), subtask=bool(d.get("subtask")),
                     hierarchy_level=d.get("hierarchyLevel"), raw=d)


def version(d: dict[str, Any]) -> Version:
    return Version(id=str(d["id"]), name=d.get("name", ""), released=bool(d.get("released")),
                   archived=bool(d.get("archived")), release_date=d.get("releaseDate"), raw=d)


def component(d: dict[str, Any]) -> Component:
    return Component(id=str(d["id"]), name=d.get("name", ""), lead=user(d.get("lead")), raw=d)


def jfilter(d: dict[str, Any]) -> Filter:
    return Filter(id=str(d["id"]), name=d.get("name", ""), jql=d.get("jql"), owner=user(d.get("owner")), raw=d)


def dashboard(d: dict[str, Any]) -> Dashboard:
    return Dashboard(id=str(d["id"]), name=d.get("name", ""), url=d.get("view"), raw=d)


def server_info(d: dict[str, Any]) -> ServerInfo:
    return ServerInfo(base_url=d.get("baseUrl"), version=d.get("version"), deployment_type=d.get("deploymentType"),
                      server_title=d.get("serverTitle"), time_zone=d.get("serverTimeZone"), raw=d)


def board(d: dict[str, Any]) -> Board:
    return Board(id=int(d["id"]), name=d.get("name", ""), type=d.get("type"),
                 project_key=(d.get("location") or {}).get("projectKey"), raw=d)


def board_config(d: dict[str, Any]) -> BoardConfig:
    return BoardConfig(id=int(d["id"]), name=d.get("name"), filter_id=str((d.get("filter") or {}).get("id", "")) or None,
                       columns=list((d.get("columnConfig") or {}).get("columns", [])), estimation=d.get("estimation"), raw=d)


def sprint(d: dict[str, Any]) -> Sprint:
    return Sprint(id=int(d["id"]), name=d.get("name", ""), state=d.get("state"), start_date=dt(d.get("startDate")),
                  end_date=dt(d.get("endDate")), goal=d.get("goal"), raw=d)


def epic(d: dict[str, Any]) -> Epic:
    return Epic(id=int(d["id"]), key=d.get("key", ""), name=d.get("name"), done=bool(d.get("done")), raw=d)


# ---- JSM -------------------------------------------------------------------------------------------------------------
def jsm_info(d: dict[str, Any]) -> JsmInfo:
    return JsmInfo(version=d.get("version"), platform_version=d.get("platformVersion"), cloud=d.get("isLicensedForUse"), raw=d)


def service_desk(d: dict[str, Any]) -> ServiceDesk:
    return ServiceDesk(id=str(d["id"]), project_id=str(d.get("projectId", "")) or None, project_key=d.get("projectKey"),
                       project_name=d.get("projectName"), raw=d)


def request_type(d: dict[str, Any]) -> RequestType:
    return RequestType(id=str(d["id"]), name=d.get("name", ""), description=d.get("description"),
                       service_desk_id=str(d.get("serviceDeskId", "")) or None, raw=d)


def customer_request(d: dict[str, Any]) -> CustomerRequest:
    fields = {f["fieldId"]: f.get("value") for f in d.get("requestFieldValues", []) if "fieldId" in f}
    return CustomerRequest(issue_id=str(d["issueId"]), issue_key=d["issueKey"], request_type=str(d.get("requestTypeId", "")) or None,
                           service_desk_id=str(d.get("serviceDeskId", "")) or None,
                           status=(d.get("currentStatus") or {}).get("status"), reporter=user(d.get("reporter")),
                           created=dt((d.get("createdDate") or {}).get("iso8601")), summary=fields.get("summary"),
                           description=fields.get("description"), fields=fields, raw=d)


def request_comment(d: dict[str, Any]) -> RequestComment:
    return RequestComment(id=str(d["id"]), author=user(d.get("author")), body=d.get("body"), public=bool(d.get("public", True)),
                          created=dt((d.get("created") or {}).get("iso8601")), raw=d)


def sla(d: dict[str, Any]) -> SlaInfo:
    cyc = d.get("ongoingCycle") or {}
    return SlaInfo(id=str(d["id"]), name=d.get("name", ""), ongoing=bool(d.get("ongoingCycle")), breached=cyc.get("breached"),
                   paused=cyc.get("paused"), remaining_millis=(cyc.get("remainingTime") or {}).get("millis"),
                   goal_duration_millis=(cyc.get("goalDuration") or {}).get("millis"), raw=d)


def kb_article(d: dict[str, Any]) -> KbArticle:
    return KbArticle(title=d.get("title", ""), excerpt=d.get("excerpt"), url=(d.get("content") or {}).get("iframeSrc"), raw=d)


def organization(d: dict[str, Any]) -> Organization:
    return Organization(id=str(d["id"]), name=d.get("name", ""), raw=d)


# ---- Confluence ------------------------------------------------------------------------------------------------------
def content(d: dict[str, Any], *, type_: str = "page", base: str | None = None) -> Content:
    body = d.get("body") or {}
    storage = (body.get("storage") or {}).get("value")
    adf_raw = (body.get("atlas_doc_format") or {}).get("value")
    view = (body.get("view") or {}).get("value")
    adf = json.loads(adf_raw) if isinstance(adf_raw, str) and adf_raw else None
    md = storage_to_markdown(storage) if storage is not None else (adf_to_markdown(adf) if adf else (storage_to_markdown(view) if view else None))
    ver = d.get("version") or {}
    links = d.get("_links") or {}
    webui = links.get("webui")
    return Content(id=str(d["id"]), type=d.get("type") or type_, title=d.get("title"), status=d.get("status"),
                   space_id=str(d["spaceId"]) if d.get("spaceId") else None, parent_id=str(d["parentId"]) if d.get("parentId") else None,
                   version=ver.get("number"), author_id=d.get("authorId"), created=dt(d.get("createdAt")),
                   updated=dt(ver.get("createdAt")), url=(base + webui) if base and webui else webui,
                   body_markdown=md, body_storage=storage, body_adf=adf, raw=d)


def content_version(d: dict[str, Any]) -> ContentVersion:
    return ContentVersion(number=int(d["number"]), message=d.get("message"), author_id=d.get("authorId"),
                          created=dt(d.get("createdAt")), minor_edit=d.get("minorEdit"), raw=d)


def conf_comment(d: dict[str, Any], kind: str) -> ConfluenceComment:
    body = d.get("body") or {}
    storage = (body.get("storage") or {}).get("value")
    res = d.get("resolutionStatus")
    return ConfluenceComment(id=str(d["id"]), kind=kind, body_markdown=storage_to_markdown(storage) if storage else None,
                             author_id=(d.get("version") or {}).get("authorId"), created=dt((d.get("version") or {}).get("createdAt")),
                             resolved=None if res is None else res == "resolved", parent_id=str(d["parentCommentId"]) if d.get("parentCommentId") else None, raw=d)


def conf_attachment(d: dict[str, Any]) -> CAttachment:
    return CAttachment(id=str(d["id"]), title=d.get("title", ""), media_type=d.get("mediaType"), file_size=d.get("fileSize"),
                       page_id=str(d["pageId"]) if d.get("pageId") else None, download_url=d.get("downloadLink"),
                       version=(d.get("version") or {}).get("number"), raw=d)


def label(d: dict[str, Any]) -> Label:
    return Label(id=str(d["id"]) if "id" in d else None, name=d.get("name", ""), prefix=d.get("prefix"), raw=d)


def space(d: dict[str, Any]) -> Space:
    return Space(id=str(d["id"]), key=d.get("key", ""), name=d.get("name"), type=d.get("type"), status=d.get("status"),
                 homepage_id=str(d["homepageId"]) if d.get("homepageId") else None, raw=d)


def conf_task(d: dict[str, Any]) -> Task:
    body = (d.get("body") or {}).get("storage", {}).get("value")
    return Task(id=str(d["id"]), status=d.get("status", ""), body=storage_to_markdown(body) if body else None,
                content_id=str(d.get("pageId") or d.get("blogPostId") or "") or None, due=d.get("dueAt"),
                assignee_id=d.get("assignedTo"), raw=d)


def template(d: dict[str, Any]) -> Template:
    return Template(id=str(d.get("templateId", d.get("id"))), name=d.get("name", ""), description=d.get("description"),
                    space_key=(d.get("space") or {}).get("key"), raw=d)


def search_hit(d: dict[str, Any]) -> SearchHit:
    c = d.get("content") or {}
    return SearchHit(content_id=str(c["id"]) if c.get("id") else None, type=c.get("type") or d.get("entityType"),
                     title=d.get("title") or c.get("title"), excerpt=d.get("excerpt"),
                     space_key=(d.get("resultGlobalContainer") or {}).get("title"), url=d.get("url"),
                     last_modified=dt(d.get("lastModified")), raw=d)


def restrictions(d: dict[str, Any]) -> Restrictions:
    def side(op: str) -> tuple[list[Any], list[str]]:
        r = ((d.get(op) or {}).get("restrictions") or {})
        users = [UserRef(account_id=u.get("accountId"), display_name=u.get("displayName")) for u in (r.get("user") or {}).get("results", [])]
        groups = [g.get("name", "") for g in (r.get("group") or {}).get("results", [])]
        return users, groups

    ru, rg = side("read")
    uu, ug = side("update")
    return Restrictions(read_users=ru, read_groups=rg, update_users=uu, update_groups=ug, raw=d)
