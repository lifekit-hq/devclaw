# Tasks: The change is the worker's own

**Input**: [spec.md](./spec.md), [plan.md](./plan.md) | **Branch**: `045-the-change-is-the-workers-own`

One PR, three stories, landed together (each story is independently
revertable by file set; none is useful without the fs-431 shape they share).

## Phase 1 — US1: the judged span is the worker's own

- [X] T001 `devclaw/task_change.py`: `ChangeSet.base_ref` / `.note`; `base_ref_sync` (delivery's ladder); `own_paths_sync` (merge-base + `--name-only`)
- [X] T002 `devclaw/task_git.py`: `_git_diff_sync(..., paths=None)` renders the span's own paths; `[]` is an empty span
- [X] T003 `devclaw/queue/settle.py`: `_capture_change` filters entries + re-renders the diff over kept paths; `base_branch` threaded from the task row; `note` when unfiltered or when paths dropped; `base_ref`/`note` serialized into the task result
- [X] T004 `tests/test_materialize_gate.py`: class case — a merge of the default branch carrying a workflow change passes `change_class`; a worker's own gate-input edit in the same run still fails naming that path only

## Phase 2 — US2: a conflicting PR is a conflict, never a wait

- [X] T005 `devclaw/goal/remote_checks.py`: `mergeable` on the same read; `combine_states(..., mergeable=)` → `conflicting` ahead of every rollup reading
- [X] T006 `devclaw/goal/tick_donegate.py`: `_route_conflict` (heal or park) + `_park_merge_failed` extracted; called from `_close_and_merge` (merge CONFLICT and the pre-merge read), `_open_done_gate`, `_finalize_accepted_close`, `_finalize_pending_merge`; the conflict steering names the gate-input rule
- [X] T007 `devclaw/goal/tick_guards.py`: `_autoheal_ci` lifts the hold on a `conflicting` read
- [X] T008 `devclaw/goal/tick_settle.py`: the settle-time advisory keeps its log line, loses its owner ping (FR-009)
- [X] T009 `devclaw/goal/service.py`: `resume_goal` refunds `merge_heal_attempted` (FR-007)
- [X] T010 `tests/test_merge_on_close.py` + `tests/test_goal_tick.py`: conflicting at the accepted close (heal unspent → increment; spent → park), at the gate open, and on a hold recheck; the settle-ping test rewritten; the resume test asserts the refund

## Phase 3 — US3: the owner's accept_close outranks the machine's concerns

- [X] T011 `devclaw/goal/decisions.py`: `MACHINE_EVAL_SOURCE`, `outranks_accept(rows)`; `tick_context` writes corrections under the constant
- [X] T012 `devclaw/goal/store/content.py`: `unread_steering_sources` (rows with `source`)
- [X] T013 `devclaw/goal/tick.py`: the plan gate closes on an owner accept_close unless a non-`auto-eval` row is unread; the accepted rows ride the close as follow-ups
- [X] T014 `devclaw/goal/project_hold.py`: `next_move` reads the same predicate (lane-free close with only `auto-eval` rows unread)
- [X] T015 `tests/test_merge_on_close.py`: `auto-eval` → closes on the tick, rows consumed, follow-ups logged; `denys` / `auto-ci` → dispatches first

## Phase 4 — docs and headers

- [X] T016 `CLAUDE.md`, `docs/architecture.md`, `docs/flows/task-execution.md`, `docs/flows/delivery.md`, `docs/INDEX.md` currency tags
- [X] T017 spec headers 013 / 025 / 032 / 041 carry the amendment line

## Live leg (after deploy)

- [ ] T018 `resume_goal fs-431-hygiene-sentinels-2026-08-31` once the merged main is deployed; watch: accept stands → `conflicting` → resolution increment → CI on the merged head → merge-on-close. SC-001 is met when the goal closes with no other owner verb.
