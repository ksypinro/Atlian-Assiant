"""Official Atlassian CLI (``acli``) command map, checked against ``acli 1.3.39 --help`` on a live Cloud site (VER-03).

What the live install showed:
- acli has no transition-list, field-list, issue-type, status, priority, server-info, user, component or version commands, and no
  attachment download or upload. Those operations are left out; the router falls back to REST / MCP.
- ``--from-json`` reads a *file* in acli's own schema (``--generate-json``), not REST JSON on stdin. Writes are therefore expressed with flags only
  (``jvars`` + ``strict``). A body the flags cannot express is refused, and the router falls back. Descriptions and comment bodies go through
  ``--description-file`` / ``--body-file`` (``files``), so they never appear in argv; the summary and keys do.
- ``workitem search --fields`` only accepts acli's display columns (no ``created`` / ``updated``). Search therefore always returns the full issue
  objects, and the facade's field selection is applied by the normalizer.
- ``--count`` prints a sentence, not JSON (``kind="count"``). ``project list`` needs ``--limit`` or ``--paginate``.
- ``view`` / ``list`` subcommands take ``--key`` / ``--id`` flags (only ``workitem view`` takes a positional key).

The CapabilityProbe still drops any operation whose subcommand or ``requires`` flags the installed ``acli`` does not advertise.
"""
from __future__ import annotations

from ..commandmap import Command as C
from ..commandmap import CommandMap

_WI = ("jira", "workitem")

ACLI_MAP = CommandMap("acli", [
    # ---- read
    C("jira.issue.get", (*_WI, "view", "{key}", "--fields", "*all", "--json"), path="/issue/{key}", requires=("--fields", "--json")),
    C("jira.issue.search", (*_WI, "search", "--jql", "{jql}", "--json", "--paginate"), "POST", "/search/jql", wrap="token:issues",
      requires=("--jql", "--json", "--paginate")),
    C("jira.issue.count", (*_WI, "search", "--jql", "{jql}", "--count"), "POST", "/search/approximate-count", kind="count", requires=("--jql", "--count")),
    C("jira.issue.comments.list", (*_WI, "comment", "list", "--key", "{key}", "--json", "--paginate"), path="/issue/{key}/comment", wrap="offset:comments",
      requires=("--key", "--json", "--paginate")),
    C("jira.issue.attachments.list", (*_WI, "attachment", "list", "--key", "{key}", "--json"), path="/issue/{key}", wrap="fields:attachment", requires=("--key", "--json")),
    C("jira.issue.links.list", (*_WI, "link", "list", "--key", "{key}", "--json"), path="/issue/{key}", wrap="fields:issuelinks", requires=("--key", "--json")),
    C("jira.issue.watchers.list", (*_WI, "list-watchers", "--key", "{key}", "--json"), path="/issue/{key}/watchers", requires=("--key", "--json")),
    # ---- write (flags only; see the module notes)
    C("jira.issue.create", (*_WI, "create", "--project", "{project}", "--type", "{issuetype}", "--summary", "{summary}", ("--description-file", "{description}"),
                            ("--label", "{labels}"), ("--parent", "{parent}"), ("--assignee", "{assignee}"), "--json"), "POST", "/issue",
      jvars=(("project", "fields.project.key"), ("issuetype", "fields.issuetype.name"), ("summary", "fields.summary"), ("description", "fields.description"),
             ("labels", "fields.labels[]"), ("parent", "fields.parent.key"), ("assignee", "fields.assignee.accountId")), strict=True, files=("description",),
      requires=("--project", "--type", "--summary", "--json")),
    C("jira.issue.edit", (*_WI, "edit", "--key", "{key}", ("--summary", "{summary}"), ("--description-file", "{description}"), ("--labels", "{labels}"),
                          ("--assignee", "{assignee}"), "--yes", "--json"), "PUT", "/issue/{key}", query=("notifyUsers",),
      jvars=(("summary", "fields.summary"), ("description", "fields.description"), ("labels", "fields.labels[]"), ("assignee", "fields.assignee.accountId")),
      strict=True, files=("description",), requires=("--key", "--yes")),
    C("jira.issue.assign", (*_WI, "assign", "--key", "{key}", "--assignee", "{accountId}", "--yes"), "PUT", "/issue/{key}/assignee", requires=("--assignee", "--yes"),
      jvars=(("accountId", "accountId"),)),
    C("jira.issue.comment.add", (*_WI, "comment", "create", "--key", "{key}", "--body-file", "{body}", "--json"), "POST", "/issue/{key}/comment",
      requires=("--key", "--body-file"), jvars=(("body", "body"),), strict=True, files=("body",)),
    C("jira.issue.link.create", (*_WI, "link", "create", "--out", "{outward}", "--in", "{inward}", "--type", "{type}", "--yes"), "POST", "/issueLink",
      jvars=(("type", "type.name"), ("inward", "inwardIssue.key"), ("outward", "outwardIssue.key")), strict=True, requires=("--out", "--in", "--type", "--yes")),
    C("jira.issue.delete", (*_WI, "delete", "--key", "{key}", "--yes"), "DELETE", "/issue/{key}", requires=("--key", "--yes"), query=("deleteSubtasks",)),
    C("jira.issue.comment.delete", (*_WI, "comment", "delete", "--key", "{key}", "--id", "{comment_id}"), "DELETE", "/issue/{key}/comment/{comment_id}",
      requires=("--key", "--id")),
    C("jira.issue.attachment.delete", (*_WI, "attachment", "delete", "--id", "{attachment_id}"), "DELETE", "/attachment/{attachment_id}", requires=("--id",)),
    # ---- projects / metadata
    C("jira.project.list", ("jira", "project", "list", "--paginate", "--json"), path="/project/search", wrap="offset:values", requires=("--paginate", "--json")),
    C("jira.project.get", ("jira", "project", "view", "--key", "{key}", "--json"), path="/project/{key}", requires=("--key", "--json")),
    C("jira.meta.link_types.list", (*_WI, "link", "type", "--json"), path="/issueLinkType", requires=("--json",)),
    C("jira.filter.list", ("jira", "filter", "search", ("--name", "{filterName}"), "--paginate", "--json"), path="/filter/search", wrap="offset:values",
      requires=("--paginate", "--json")),
    C("jira.dashboard.list", ("jira", "dashboard", "search", "--paginate", "--json"), path="/dashboard/search", wrap="offset:values", requires=("--paginate", "--json")),
    # ---- agile
    C("jira.agile.board.list", ("jira", "board", "search", ("--project", "{projectKeyOrId}"), ("--name", "{name}"), "--paginate", "--json"), path="/board",
      surface="agile", wrap="offset:values", requires=("--paginate", "--json")),
    C("jira.agile.board.get", ("jira", "board", "view", "--id", "{board_id}", "--json"), path="/board/{board_id}", surface="agile", requires=("--id", "--json")),
    C("jira.agile.sprint.list", ("jira", "board", "list-sprints", "--id", "{board_id}", ("--state", "{state}"), "--paginate", "--json"), path="/board/{board_id}/sprint",
      surface="agile", wrap="offset:values", requires=("--id", "--paginate", "--json")),
    C("jira.agile.sprint.get", ("jira", "sprint", "view", "--id", "{sprint_id}", "--json"), path="/sprint/{sprint_id}", surface="agile", requires=("--id", "--json")),
    # ---- export
    C("export.jira.csv", (*_WI, "search", "--jql", "{jql}", "--paginate", "--csv"), "POST", "/search/jql", kind="csv", requires=("--paginate", "--csv")),
])
