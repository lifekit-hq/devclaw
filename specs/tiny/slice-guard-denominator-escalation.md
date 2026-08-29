# slice-guard-denominator-escalation

## What
Two defects in the dispatch-boundary single-feature slice guard (issue #679,
`devclaw/goal/tick_dispatch.py` calling `slice_guard.speckit_feature_state_sync`):

1. **Wrong denominator.** The guard counted unchecked task rows across the
   ENTIRE speckit history, not the features the current goal is advancing.
   A mature repo with accumulated historical feature dirs (any leftover unchecked
   rows) became permanently undispatchable. Observed live: finance-sentry blocked
   on 5 historical dirs, 14+ consecutive ticks, `actions_dispatched: 0`.

2. **Silent hold.** The guard returned `Outcome.SLEPT` unconditionally —
   no state transition, no `blocked_kind`, no owner ping, no bound. Contradicts
   the "loud failure over silent degradation" invariant.

## Context
Guard code: `devclaw/goal/slice_guard.py:speckit_feature_state_sync` (detection),
`devclaw/goal/tick_dispatch.py:183` (enforcement).

Live measurement 2026-08-28: total=40, graded=32, active=5 in the finance-sentry
workspace. The 5 offenders (`001-bank-account-sync`, `011-connect-providers`,
`021-market-regime`, `037-structured-data-sources`, `039-ips-risk-boundary`)
are historical — not part of the current goal's work.

## Requirements
- [x] Add `speckit_offending_dirs_sync(workspace_dir, current_dir)` to
  `slice_guard.py`: returns active feature dirs modified at-or-after
  `current_dir`'s tasks.md (concurrent = same-session build-ahead signal).
  Dirs older than `current_dir` are historical → excluded. Empty `current_dir`
  → `[]` (fail-open, all historical). Zero-token, never-raises.
- [x] Update the dispatch gate to scope the `active > 1` check: call
  `current_feature_dir_sync` then `speckit_offending_dirs_sync`; hold only
  when offending dirs are non-empty.
- [x] Add `slice_hold_count: int` to `GoalStatus` + SQLite migration.
  Increment on each hold; at `_SLICE_HOLD_CAP` (5) consecutive holds,
  transition to `blocked` with `blocked_kind="mechanical:slice_hold"` naming
  the offending dirs. Reset on guard pass and human steer/resume.
- [x] Named regression: finance-sentry fixture (40 dirs, 32 graded, 5 active)
  asserts dispatch proceeds (`test_dispatch_proceeds_with_historical_active_features`).
- [x] Named regression: concurrent build-ahead is still caught
  (`test_dispatch_held_with_concurrent_build_ahead`).
- [x] Named regression: `speckit_offending_dirs_sync` unit tests including the
  finance-sentry fixture with mtime-based historical exclusion.
- [x] Named regression: N consecutive holds → `blocked` with `blocked_kind` and
  reason naming the offending dirs (`test_slice_hold_cap_transitions_to_blocked_with_offending_dirs`;
  `test_slice_hold_count_resets_on_steer_and_resume`).
- [x] Doctor check: `check_goal_status_slice_hold_count` verifies the
  `slice_hold_count` column exists on a deployed instance (spec-016 FR-014:
  a persisted state shape change ships its doctor check); seeded-fault test
  `test_slice_hold_count_column_absent_detected` pins the drift class.

## Plan
- `devclaw/goal/slice_guard.py` — add `speckit_offending_dirs_sync`
- `devclaw/goal/models.py` — add `slice_hold_count` to `GoalStatus`
- `devclaw/goal/state.py` — schema + ALTER TABLE migration
- `devclaw/goal/state_status.py` — INSERT/UPDATE/`STATUS_FIELD_COLUMNS`/`_row_to_status`
- `devclaw/goal/tick_dispatch.py` — scoped check + escalation + `_SLICE_HOLD_CAP`
- `devclaw/goal/service.py` — reset `slice_hold_count=0` in steer/resume
- `tests/test_slice_guard_tasks.py` — unit tests for `speckit_offending_dirs_sync`
- `tests/test_goal_tick.py` — integration regression tests
- `tests/test_runner_oom_evidence.py` — fix pre-existing env-dep failure

## Done-When
Full criteria from issue #728: all of the above plus the escalation regression test.
Finance-sentry dispatch and zero-token guard invariant are both confirmed green.
