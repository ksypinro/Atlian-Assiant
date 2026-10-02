# Agent entry point

This repo builds the `AtlassianClient` Python library described in
`atlassian_client_architecture_design.md`. Work is tracked in `tracking/`; progress dashboard: `TRACKING.md` (generated).

**Before doing anything, read `tracking/AGENT_WORKFLOW.md`.** Short version:

```bash
python -m tools.track next --agent <you>      # pick a ready task
python -m tools.track claim <ID> --agent <you>
# ...implement ONLY the task's deliverables, to its acceptance bullets...
python -m tools.track submit <ID> --agent <you>   # runs the task's checks; refuses on failure
```

Never edit `tracking/definitions/`, `tracking/state/`, or `TRACKING.md` by hand, never mark a task done, never sign a gate.
If the design is wrong or a task is blocked, use `track block` / `track dcr file` instead of improvising.
