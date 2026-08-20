# Contract: the MCP surface after the amputation

devclaw's external contract is its MCP tool surface — the OpenClaw waiter is the
only consumer and lives outside this repo. This file is the authoritative
before/after, and doubles as the FR-020 removal inventory for the PR body.

## Removed (6) — no replacement, callers must stop

| Tool | Reason |
|---|---|
| `get_program` | program vocabulary deleted; unreachable since spec 008 |
| `list_programs` | same |
| `cancel_program` | same |
| `review_trends` | trend detector deleted; `specs/NNN-*/` is cross-session memory |
| `scope_grill` | superseded by `/specify` + `/clarify` |
| `start_program` | already deprecated sugar; the word leaves the vocabulary |

## Retained as deprecated sugar (5) — signature unchanged, implementation replaced

`dispatch_task`, `implement_feature`, `fix_bug`, `review_repository`, `onboard`

Each now files a one-shot goal via `create_goal(mode='one_shot')` and returns a
**goal id** instead of a task id. `queue.submit()` has no caller outside the
goal layer after this change.

**Breaking detail for the waiter**: the returned identifier is a goal id. A
caller that feeds the result to `get_status`/`list_tasks` must move to
`get_goal`/`list_goals`. Retiring these names is a follow-up, taken once the
waiter is confirmed migrated.

**Behavioral consequence**: a one-shot now carries a done-gate and can park
`needs_human` where a bare task would simply have failed. Intended — ADR 0003's
"ONE identical execution path".

## Unchanged (41)

Goal primitives (`create_goal`, `get_goal`, `list_goals`, `tail_goal`,
`steer_goal`, `resume_goal`, `cancel_goal`, `set_goal_strictness`,
`evaluate_goal`, `verify_goal`, `dry_evaluate`), intake (`file_intake`,
`regrade_intake`, `grade_backlog`), projects (6), deploy (6 — deferred),
repo (2), operator controls (3), observability (`list_problems`, `get_trace`,
`get_events`, `get_status`, `list_tasks`, `get_scorecard_metrics`,
`cancel_task`).

## Count

**47 → 42.** Verify: `grep -c "@mcp.tool" devclaw/server/tools.py`
