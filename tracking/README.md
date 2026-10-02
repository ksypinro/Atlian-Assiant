# Tracking system

Source of truth = files in this directory. `TRACKING.md` (repo root) is generated from them.

| Path | Purpose | Who edits |
|---|---|---|
| `definitions/generated/` | Tasks parsed from the design doc tables (`track seed`) | generator only |
| `definitions/authored/` | Hand-written infrastructure tasks and DCRs | reviewer / `dcr file` |
| `definitions/seed_hints.yaml`, `phases.yaml`, `workstreams.yaml` | Mapping hints, phase gates, workstreams | reviewer |
| `state/<ID>.yaml` | Mutable status (absent file = `todo`) | `track` CLI only |
| `gates/<PHASE>.yaml` | Gate sign-off records | `track gate sign` |
| `evidence/<ID>.md` | Agent submission evidence | `track submit` |
| `environment.yaml` | Which of credentials/sandbox/decision the humans have supplied | reviewer |

Task lifecycle: `todo -> claimed -> in_review -> done` (+ `changes_requested`, `blocked`, `wont_do`).
Phases P0..P6 (with P4b/P4c) are gated by exit criteria, no dates. Work on phase N+1 may start once gate N-1 is signed.

Health: `scripts/check.sh` (validate, check-coverage, report --check, verify-done, tracker tests).
Design drift: editing the design document makes `check-coverage` fail until `track seed` regenerates tasks and the diff is reviewed.
See `AGENT_WORKFLOW.md` and `REVIEWER_GUIDE.md`.
