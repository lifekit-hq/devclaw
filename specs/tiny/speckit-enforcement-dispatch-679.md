# speckit-enforcement-dispatch-679

## What
Speckit enforcement at the dispatch boundary fails open (issue #679):
the goal tick dispatches a worker even when the workspace has ungraded
specs or multiple features with pending tasks.

## Context
`tick_dispatch._dispatch_action` records the active speckit feature
after dispatch but never gates on it (comment: "recording is a bonus,
never a dispatch gate"). Three concrete failures:
(a) a spec dir with no `tasks.md` (the plan step never ran) → worker
    dispatches into an unplanned session;
(b) 2+ features with pending tasks → worker picks an arbitrary feature,
    violating the single-feature-per-increment contract;
(c) a 3-feature workspace → dispatched and built a mega-PR.

## Requirements
- [x] `slice_guard.speckit_feature_state_sync(workspace_dir)` → `(total_dirs, graded, active)` working-tree read; zero-token, never-raises.
- [x] At the dispatch boundary (before the fan-out), check the state:
  - `total > 0 and graded == 0` → hold with actionable log ("spec dir(s) present but none graded — complete the speckit plan step").
  - `active > 1` → hold with actionable log ("N features have pending tasks — single-feature enforcement").
  - `total == 0` (no specs yet) → allow (first dispatch).
  - `active == 1` → allow (single feature, graded).
  - Probe failure → fail OPEN (never wedge a goal on a fs error).
  - `review_repository` actions are exempt (read-only, no feature advance).
- [x] Named regression tests for all three scenarios plus fail-open.

## Plan
1. Add `speckit_feature_state_sync` to `devclaw/goal/slice_guard.py`.
2. Add enforcement block in `devclaw/goal/tick_dispatch.py` after the speckit-install hold.
3. Tests: `tests/test_slice_guard_tasks.py` (unit) + `tests/test_goal_tick.py` (integration).

## Tasks
- [x] T001 `speckit_feature_state_sync` in slice_guard.py
- [x] T002 Enforcement gate in tick_dispatch.py
- [x] T003 Unit tests for speckit_feature_state_sync
- [x] T004 Integration tests in test_goal_tick.py (a/b/c + fail-open + first-dispatch)

## Done-When
`pytest tests/test_slice_guard_tasks.py tests/test_goal_tick.py -q` green; the three acceptance scenarios (a)/(b)/(c) are named regression tests; the fix is committed and PR open.
