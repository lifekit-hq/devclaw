# Implementation Plan: The change is the worker's own

**Branch**: `045-the-change-is-the-workers-own` | **Date**: 2026-09-10 | **Spec**: [spec.md](./spec.md)

## Summary

Three seams read a fact the loop already holds and act on a worse one. Each
story moves the deciding seam onto the fact it already had — no new
mechanism, no new column, no cognition:

| Story | The fact the loop holds | The seam that ignored it | The change |
|---|---|---|---|
| US1 | the base branch (it clones from it, delivers to it) | `task_change` judged `pre..post` as the tree delta | the span is `pre..post` less what the base carries at the merge-base with `post`; ONE place (`queue/settle._capture_change`), every consumer inherits |
| US2 | `mergeable` on the same `gh pr view` the rollup comes from | the CI hold read only the rollup | `RemoteChecksResult.state == "conflicting"`; ONE routing (`tick_donegate._route_conflict`) shared by the merge attempt's CONFLICT and the reader's state, called at the gate open, the accepted close, the merge-time hold, the pending-merge retry, and the CI auto-heal (which lifts the hold instead of waiting) |
| US3 | the steering row's `source` | the plan gate read only "any unread row" | `decisions.outranks_accept(rows)`: only a non-`auto-eval` row dispatches before an owner accept_close; the close consumes `auto-eval` rows as follow-ups; `project_hold.next_move` reads the same predicate so the lane never queues the close |

Plus FR-007: `resume_goal` restores `merge_heal_attempted`, the one heal
budget it did not already restore.

## Technical Context

- **Layers touched**: 4 (`queue/settle.py`, `task_change.py`, `task_git.py`)
  for US1; 2 (`goal/remote_checks.py`, `goal/tick_donegate.py`,
  `goal/tick_guards.py`, `goal/tick.py`, `goal/project_hold.py`,
  `goal/decisions.py`, `goal/store/content.py`, `goal/service.py`,
  `goal/tick_settle.py`) for US2/US3. No layer reaches through another;
  `lint-imports` unchanged.
- **Constitution check**: III zero-token idle — no new cognition, no new
  tick-path subprocess on an idle/blocked goal (`mergeable` rides the read
  the hold already makes; the base-ref/merge-base reads run only inside a
  settle's materialize, never on a tick). IV single writer — the span filter
  is inside the ONE capture; the conflict routing is inside the CAS'd
  transitions. V fail-closed — an unfilterable span stays unfiltered (judges
  MORE), a `conflicting` read never proceeds. VI loud — the drop is written
  into the change (`note`) and the goal log. VII class — three seams, one
  shape, one fix each. IX — software owns the verdict of record (the span,
  the CI fact); the worker is told one more sentence (take the base's side
  for a gate-input file) and nothing else.
- **State shape**: no new columns; `ChangeSet` gains `base_ref`/`note` (a
  task result blob, not persisted state) — no doctor check owed.
- **Tests (tripwire classes)**: the materialize span
  (`tests/test_materialize_gate.py`), fail-closed gates + brakes
  (`tests/test_merge_on_close.py`, `tests/test_goal_tick.py`), the resume
  budget refund (existing service-level resume test extended). The
  settle-time owner-ping test is rewritten to the retired-ping behaviour
  (symmetric ratchet).

## Rejected at plan time

- Threading `base_branch` through every consumer: the capture resolves the
  base ref itself with delivery's ladder; the row's `base_branch` is passed
  when the settle has it.
- A `conflicting` branch inside `_ci_hold_text` / the hold: the hold is
  never entered; routing before it is the whole point.
- Filtering by content equality per path (`git diff base -- path` per
  entry): one `merge-base` + one `--name-only` diff answers all paths.
