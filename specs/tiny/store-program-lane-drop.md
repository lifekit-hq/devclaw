# TinySpec: Drop the program-lane store remnants (022 demolition tail)

**Branch**: refactor/store-program-lane-drop
**Date**: 2026-08-30
**Status**: done
**Complexity**: small

## What

Spec 022 US3 demolished the program/DAG dispatch lane in code, but the store
never caught up. The live DB still carries: the orphan `programs` table (34
rows, zero code references), 79 zombie pending tasks with `program_id` set
(2026-06-23→07-30 — undispatchable ONLY because the pending scan filters
`program_id IS NULL`), dead columns (`tasks.program_id/depends_on/order_idx/
lane_json`, `events.program_id`, `eval_outcomes.program_id`), dead indexes,
the #641-era `projects.automerge`/`merge_strategy` columns, and 215 stale
`trend_*` meta keys pinned to long-deleted workspaces. This PR finishes the
demolition: code stops threading the dead fields, schema-ensure drops the
remnants, and trend meta becomes self-cleaning.

## Context

| File | Role |
|------|------|
| `devclaw/state_store/schema.py` | Modified — tasks/events/eval_outcomes CREATE stanzas + ALTERs lose the dead columns; new demolition block (order: delete zombies → drop indexes → drop table → drop columns) |
| `devclaw/state_store/rows.py` | Modified — `Task` loses program_id/depends_on/order_idx/lane_json; `TaskEvent` loses program_id; mappers + to_dict |
| `devclaw/state_store/core.py` | Modified — `create_task` param + INSERT; pending scan drops the `program_id IS NULL` filter (safe only because zombies are deleted first) |
| `devclaw/state_store/observability.py` | Modified — `append_event` loses program_id |
| `devclaw/state_store/evals.py` | Modified — eval INSERT loses program_id |
| `devclaw/task_queue.py`, `devclaw/queue/settle.py`, `devclaw/queue/admission.py` | Modified — remove the vestigial program_id threading |
| `devclaw/project_registry.py` | Modified — idempotent DROP COLUMN automerge/merge_strategy |
| `devclaw/state_store/control.py` + `devclaw/trend_detector.py` + `devclaw/goal/tick.py` | Modified — `prune_stale_trend_meta`: trend keys for unregistered workspaces are deleted each harness sweep (keys now expire with the project — the class fix, not a one-off wipe) |
| `devclaw/doctor/checks_instance.py` | Modified — FR-014: `instance.legacy.program_lane` check (programs table gone, no program_id column, zero zombie pending) |
| tests | `program_id=None` kwargs removed (symmetric ratchet); doctor seeded-fault case added |

## Requirements

1. Migration ORDER is load-bearing: DELETE zombie pending rows (`status='pending'
   AND program_id IS NOT NULL`) BEFORE dropping `tasks.program_id` — once the
   column is gone the pending scan can no longer filter them out.
2. All drops idempotent: `DROP TABLE/INDEX IF EXISTS`; DROP COLUMN swallows
   "no such column" (mirrors the add-column idiom).
3. `milestone` and `plan_key` STAY — still read by `gate_policy.py` /
   `routes/_common.py`.
4. Trend meta self-cleans: on each harness sweep, `trend_cooldown:project:*`,
   `trend_fingerprint:project:*`, `trend_bookmark:*` keys whose workspace is
   not currently registered are deleted. Harness-self scope keys are never
   touched.
5. Doctor check `instance.legacy.program_lane` fails on: programs table
   present, tasks.program_id present, or pending rows that no scan can reach —
   remedy "restart devclaw (demolition migration runs at boot)". Seeded-fault
   test in the doctor suite.
6. API shape: `Task.to_dict` loses programId/dependsOn/orderIdx; `TaskEvent.
   to_dict` loses programId. Nothing in server routes or console reads them
   (verified by grep — the program surfaces were pruned 2026-08-29).

## Plan

1. rows/core/observability/evals/task_queue/settle/admission: mechanical
   removal of the dead fields.
2. schema.py demolition block per requirement 1–2.
3. project_registry.py `_drop_column` for the two #641 columns.
4. control.py `prune_stale_trend_meta` + detector `prune_stale_scopes` +
   tick call beside `run_harness_self` (guarded by `project_workspaces`).
5. Doctor check + seeded-fault test; update tests' `program_id=` kwargs.

## Tasks

- [x] Remove program-lane fields from store dataclasses + writers + queue threading
- [x] Schema demolition block (zombies → indexes → table → columns)
- [x] projects.automerge / merge_strategy drop
- [x] Trend meta self-clean (control + detector + tick)
- [x] Doctor `instance.legacy.program_lane` + seeded-fault test
- [x] Update existing tests (symmetric ratchet); suite + ruff + mypy green

## Done When

- [x] Live DB after next boot: no programs table, no program-lane columns, 1 real pending task, no dead-workspace trend keys after first sweep
- [x] Doctor green on the new check; suite green; PR open
