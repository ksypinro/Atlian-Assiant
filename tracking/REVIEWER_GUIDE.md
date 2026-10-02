# Reviewer guide

- **Dashboard:** `python -m tools.track report` regenerates `TRACKING.md` (section 2 = what needs you now).
- **Review queue:** `review queue`; read `tracking/evidence/ID.md` (checks, files, out-of-scope files) and the diff.
  `review approve ID --reviewer NAME --note "..."` or `review reject ID --note "what to fix"`.
- **Human-only tasks** (SEC-*, VER-*, GATE-*): do the work or confirm it, then `review approve ID` directly.
- **Unblocking:** `blocked` lists items by need. Provide the resource, add it to `tracking/environment.yaml`
  (`available: [credentials, sandbox, decision]`), then `unblock ID`.
- **Design changes:** `dcr approve DCR-nnn`, edit the design document, run `seed` then `check-coverage`.
- **Gates:** `gate status`; `gate sign P0 --reviewer NAME` runs the phase's machine checks and refuses if any task is not done,
  the previous gate is unsigned, or a done task has regressed (`verify-done`).
- Weekly: `verify-done` (also in `scripts/check.sh`) re-runs checks of all done tasks.
