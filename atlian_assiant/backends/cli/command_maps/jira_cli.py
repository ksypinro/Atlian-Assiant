"""Community ``ankitpokhrel/jira-cli`` command map. THIRD-PARTY and UNVERIFIED against a live install (VER-05: raw / JSON output availability per command).

Where a command has no machine-readable mode the CapabilityProbe drops it; bodies the flags cannot express are refused (``strict``) rather than silently
truncated, so the router falls back to REST. Targets Jira Cloud and on-prem (design 10.7).
"""
from __future__ import annotations

from ..commandmap import Command as C
from ..commandmap import CommandMap

_I = ("issue",)                                  # the binary itself is `jira`: argv starts at the subcommand

JIRA_CLI_MAP = CommandMap("jira-cli", [
    C("jira.issue.get", (*_I, "view", "{key}", "--raw"), path="/issue/{key}", requires=("--raw",)),
    C("jira.issue.search", (*_I, "list", "--jql", "{jql}", ("--fields", "{fields}"), "--raw"), "POST", "/search/jql", wrap="token:issues", requires=("--jql", "--raw")),
    C("jira.issue.count", (*_I, "list", "--jql", "{jql}", "--count"), "POST", "/search/approximate-count", requires=("--jql", "--count")),
    C("jira.issue.comments.list", (*_I, "comment", "list", "{key}", "--raw"), path="/issue/{key}/comment", wrap="offset:comments", requires=("--raw",)),
    C("jira.issue.transitions.list", (*_I, "move", "{key}", "--list", "--raw"), path="/issue/{key}/transitions", requires=("--list",)),
    C("jira.issue.create", (*_I, "create", "-p", "{project}", "-t", "{type}", "-s", "{summary}", ("-b", "{description}"), ("-a", "{assignee}"), ("-l", "{labels}"),
                           ("-P", "{parent}"), "--no-input", "--raw"), "POST", "/issue", requires=("--no-input", "--raw"), strict=True,
      jvars=(("project", "fields.project.key"), ("type", "fields.issuetype.name"), ("summary", "fields.summary"), ("description", "fields.description"),
             ("assignee", "fields.assignee.accountId"), ("labels", "fields.labels[]"), ("parent", "fields.parent.key"))),
    C("jira.issue.edit", (*_I, "edit", "{key}", ("-s", "{summary}"), ("-b", "{description}"), "--no-input"), "PUT", "/issue/{key}", requires=("--no-input",), strict=True,
      query=("notifyUsers",), jvars=(("summary", "fields.summary"), ("description", "fields.description"))),
    C("jira.issue.assign", (*_I, "assign", "{key}", "{accountId}"), "PUT", "/issue/{key}/assignee", jvars=(("accountId", "accountId"),), strict=True),
    C("jira.issue.transition", (*_I, "move", "{key}", "{transition_id}", "--no-input"), "POST", "/issue/{key}/transitions", requires=("--no-input",), strict=True,
      jvars=(("transition_id", "transition.id"),)),
    C("jira.issue.comment.add", (*_I, "comment", "add", "{key}", "{body}", "--no-input"), "POST", "/issue/{key}/comment", requires=("--no-input",), strict=True,
      jvars=(("body", "body"),)),
    C("jira.issue.comment.edit", (*_I, "comment", "edit", "{key}", "{comment_id}", "{body}", "--no-input"), "PUT", "/issue/{key}/comment/{comment_id}", requires=("--no-input",),
      strict=True, jvars=(("body", "body"),)),
    C("jira.issue.worklog.add", (*_I, "worklog", "add", "{key}", "{timeSpent}", "--no-input"), "POST", "/issue/{key}/worklog", requires=("--no-input",), strict=True,
      jvars=(("timeSpent", "timeSpent"),)),
    C("jira.issue.link.create", (*_I, "link", "{inward}", "{outward}", "{link_type}"), "POST", "/issueLink", strict=True,
      jvars=(("inward", "inwardIssue.key"), ("outward", "outwardIssue.key"), ("link_type", "type.name"))),
    C("jira.issue.remote_link.create", (*_I, "link", "remote", "{key}", "{url}", "{title}"), "POST", "/issue/{key}/remotelink", strict=True,
      jvars=(("url", "object.url"), ("title", "object.title"))),
    C("jira.issue.delete", (*_I, "delete", "{key}", ("--cascade", "{deleteSubtasks}")), "DELETE", "/issue/{key}", query=("deleteSubtasks",), requires=("--cascade",)),
    C("jira.issue.comment.delete", (*_I, "comment", "delete", "{key}", "{comment_id}"), "DELETE", "/issue/{key}/comment/{comment_id}"),
    C("jira.issue.attachment.delete", (*_I, "attachment", "delete", "{attachment_id}"), "DELETE", "/attachment/{attachment_id}"),
    C("jira.project.list", ("project", "list", ("--query", "{query}"), "--raw"), path="/project/search", wrap="offset:values", requires=("--raw",)),
    C("jira.project.get", ("project", "view", "{key}", "--raw"), path="/project/{key}", requires=("--raw",)),
    C("jira.project.components.list", ("project", "components", "{project}", "--raw"), path="/project/{project}/components", requires=("--raw",)),
    C("jira.project.versions.list", ("release", "list", "--project", "{project}", "--raw"), path="/project/{project}/versions", requires=("--project", "--raw")),
    C("jira.agile.board.list", ("board", "list", ("--project", "{projectKeyOrId}"), ("--name", "{name}"), "--raw"), path="/board", surface="agile", wrap="offset:values",
      requires=("--raw",)),
    C("jira.agile.board.get", ("board", "view", "{board_id}", "--raw"), path="/board/{board_id}", surface="agile", requires=("--raw",)),
    C("jira.agile.board.config", ("board", "config", "{board_id}", "--raw"), path="/board/{board_id}/configuration", surface="agile", requires=("--raw",)),
    C("jira.agile.board.issues", ("board", "issues", "{board_id}", ("--jql", "{jql}"), "--raw"), path="/board/{board_id}/issue", surface="agile", wrap="offset:issues",
      requires=("--raw",)),
    C("jira.agile.sprint.list", ("sprint", "list", "{board_id}", ("--state", "{state}"), "--raw"), path="/board/{board_id}/sprint", surface="agile", wrap="offset:values",
      requires=("--raw",)),
    C("jira.agile.sprint.get", ("sprint", "view", "{sprint_id}", "--raw"), path="/sprint/{sprint_id}", surface="agile", requires=("--raw",)),
    C("jira.agile.sprint.issues", ("sprint", "issues", "{sprint_id}", ("--jql", "{jql}"), "--raw"), path="/sprint/{sprint_id}/issue", surface="agile",
      wrap="offset:issues", requires=("--raw",)),
    C("jira.agile.backlog.list", ("board", "backlog", "{board_id}", "--raw"), path="/board/{board_id}/backlog", surface="agile", wrap="offset:issues", requires=("--raw",)),
    C("jira.agile.epic.list", ("epic", "list", "{board_id}", "--raw"), path="/board/{board_id}/epic", surface="agile", wrap="offset:values", requires=("--raw",)),
    C("jira.agile.issue.move_to_sprint", ("sprint", "add", "{sprint_id}", "{issues}"), "POST", "/sprint/{sprint_id}/issue", surface="agile", strict=True,
      jvars=(("issues", "issues[]"),)),
])
