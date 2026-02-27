# Worker Assignment: worker-2

**Team:** read-omx-plans-vibetrading-ref
**Role:** executor
**Worker Name:** worker-2

## Your Assigned Tasks

- **Task 2**: Worker 2 bootstrap
  Description: Coordinate on: Read .omx/plans/vibetrading_refactor_prd.md AND .omx/plans/vibetrading_import_hotspots.md. Generate data/, execution/, runner/, strategies/ folder as suggested Target tree in vibetrading_V2/.

Report findings/results back to the lead and keep task updates current.
  Status: pending

## Instructions

1. Load and follow `skills/worker/SKILL.md`
2. Send startup ACK to the lead mailbox using MCP tool `team_send_message` with `to_worker="leader-fixed"`
3. Start with the first non-blocked task
4. Read the task file for your selected task id at `.omx/state/team/read-omx-plans-vibetrading-ref/tasks/task-<id>.json` (example: `task-1.json`)
5. Task id format:
   - State/MCP APIs use `task_id: "<id>"` (example: `"1"`), not `"task-1"`.
6. Request a claim via state API (`claimTask`) to claim it
7. Complete the work described in the task
8. Write `{"status": "completed", "result": "brief summary"}` to the task file
9. Write `{"state": "idle"}` to `.omx/state/team/read-omx-plans-vibetrading-ref/workers/worker-2/status.json`
10. Wait for the next instruction from the lead
11. For team_* MCP tools, do not pass `workingDirectory` unless the lead explicitly asks

## Scope Rules
- Only edit files described in your task descriptions
- Do NOT edit files that belong to other workers
- If you need to modify a shared/common file, write `{"state": "blocked", "reason": "need to edit shared file X"}` to your status file and wait
- Do NOT spawn sub-agents (no `spawn_agent`). Complete work in this worker session.
