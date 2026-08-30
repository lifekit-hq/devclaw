# TinySpec: Retention for settled tasks' result_json

**Branch**: feat/task-result-retention
**Date**: 2026-08-30
**Status**: done
**Complexity**: small

## What

`tasks.result_json` holds full worker transcripts on settled tasks forever —
83.5MB of the 277MB live DB (single rows up to 918KB, back to June). Events
and traces both have daily retention prunes; task results have none. Add a
matching retention pass: settled (done/failed/cancelled) tasks older than
`DEVCLAW_TASK_RESULT_RETENTION_DAYS` (default 30) get `result_json` set to
NULL. The settle summary lives on the row (status/error/pr_url) and in
`eval_outcomes` — nothing decision-relevant is lost; the weekly vacuum
reclaims the disk.

## Context

| File | Role |
|------|------|
| `devclaw/config.py` | Modified — `task_result_retention_days_raw()` (single doorway) |
| `devclaw/state_store/observability.py` | Modified — default + parser + `maybe_compact_task_results` (same daily watermark/batch semantics as the prunes) |
| `devclaw/goal/engine.py` | Modified — `compact_task_results()` seam beside `prune_events()` |
| `devclaw/goal/tick.py` | Modified — driven on the heartbeat's cheap path beside the other prunes |
| `docs/reference/env-vars.md` | Modified — new row (doc↔code parity is test-enforced) |
| `tests/test_state_store_guards.py` | Modified — extend the retention guard: old settled compacts, fresh/unsettled survive, 0 disables |

## Requirements

1. Only settled rows (`status IN ('done','failed','cancelled')`) with
   `completed_at` older than the cutoff are touched; `result_json` becomes
   NULL; `error`, `pr_url`, `goal`, and every other column are untouched.
2. Running/pending rows are NEVER touched regardless of age.
3. Same operational envelope as the prunes: at most one cycle per day (meta
   watermark `task_result_compact_last_ms`), batched, pure SQLite, zero LLM,
   `<= 0` disables.
4. Env var reads through `devclaw/config.py`; `docs/reference/env-vars.md`
   row added (bidirectional doc-sync test keeps it honest).

## Plan

Mirror the `maybe_prune_events` shape end to end: config raw-reader → parsed
days → store method → engine seam → tick helper. Extend the state-store guard
tests.

## Tasks

- [x] Config doorway + parsed reader
- [x] `maybe_compact_task_results` in the store
- [x] Engine seam + tick wiring
- [x] env-vars doc row
- [x] Extend retention guard test; suite + ruff + mypy green

## Done When

- [x] Old settled transcripts compact on the next deployed heartbeat; fresh and in-flight rows never do
- [x] Suite green; PR open
