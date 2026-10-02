# Agent workflow

All commands: `python -m tools.track <cmd>` (use the repo's `.venv`).

## Loop
1. `next --agent NAME` - lists ready tasks, best first (lowest phase, longest dependency chain, smallest size).
   Human-only tasks, tasks needing missing credentials/sandbox, and tasks beyond the open phase window are never offered.
2. `show ID` - full definition and state. `claim ID --agent NAME` - 4-hour lease, **one task at a time**.
   Expired leases are released automatically.
3. Read the task's `design_refs` (section numbers in `atlassian_client_architecture_design.md`) and `research_refs`.
   Implement **exactly** the acceptance bullets, touching only the declared `deliverables`.
4. `submit ID --agent NAME [--commit HASH]` - runs every command in `checks`; if any fails nothing is submitted.
   On success it writes `tracking/evidence/ID.md` and moves the task to `in_review`.
5. If review rejects the task it returns to you as `changes_requested` (still your lease); fix and submit again.

## Blocked or the design is wrong
- Missing credentials, sandbox or a decision: `block ID --agent NAME --needs credentials|sandbox|decision --reason "..."`.
- Design needs to change: `dcr file --title "..." --reason "..." --phase P0 --refs §7.2` and block the task on it.
  Do **not** edit the design document yourself.

## Rules
- Never edit `tracking/definitions/**`, `tracking/state/**`, `tracking/gates/**`, `TRACKING.md`.
- Never mark a task `done` or sign a gate; only the reviewer can.
- Write tests first where `checks` name a test file; the check must exercise the behaviour, not just import it.
- Task IDs: `MX.*` = operation x backend cell, `DM-nn.P/.C` = delivery method provisioner/consumer, `PROTO.*` contract suites,
  `TOOL-nn` agent tools, `SEC-nn` security controls (human reviewed), `VER-nn` live-doc verification (human).
