# TinySpec: the merge-conflict heal survives the closed-issues freshness guard

**Issue**: none (live-instance incident, goal `issue-443-fix-lifekit-hq-f941c1`, 2026-09-03)
**Branch**: fix/merge-heal-survives-freshness-guard
**Date**: 2026-09-03
**Status**: implemented
**Complexity**: small

## What

Spec 025 FR-017 grants an achieved goal ONE bounded conflict-resolution
increment when its cumulative PR cannot merge. On an issue-referenced goal
whose issues are all closed, that increment never ran: the heal returned the
goal to idle with `donegate_rounds` reset to 0, and the dispatch-boundary
freshness guard (#726) read that exact state as "all issues closed, no prior
refusal → propose done without dispatching a worker". The done-gate achieved
again, the merge hit the same CONFLICT, and the goal parked
`mechanical:merge_failed` with its heal budget spent but never used.

Observed on the live instance 2026-09-03 between 02:42 (first conflict, heal
queued) and 03:10 (second conflict, parked) — the `2026-09-02` cycle's only
wedge.

## Context

| File | Role |
|------|------|
| `devclaw/goal/tick.py` | Modified — the freshness guard's propose-done shortcut |
| `devclaw/goal/tick_donegate.py` | Read only — the heal branch that resets the round counter |
| `tests/test_merge_on_close.py` | Modified — the class test for the heal/park state machine gains the closed-issues case |

## Requirements

1. With `merge_heal_attempted` set, the freshness guard never takes the
   "propose done without dispatching a worker" shortcut; it dispatches the
   resolution increment through the normal advance path, brief carrying the
   `[merge-conflict]` steering row.
2. The guard's other behaviours are byte-unchanged: closed issues with
   `donegate_rounds == 0` and no heal owed still propose done; closed issues
   after a refusal still dispatch a worker.
3. The heal budget stays ONE: a second CONFLICT after the increment still
   parks (existing test).
4. Tripwire: `test_merge_conflict_heal_survives_closed_referenced_issues`
   in `tests/test_merge_on_close.py` (brake-machinery class — extends the
   existing heal/park class test rather than minting a sibling).

## Plan

1. `tick.py`: add `and not base.merge_heal_attempted` to the shortcut
   condition; log a distinct line when the increment is dispatched because a
   heal is owed.
2. `tests/test_merge_on_close.py`: `_tick` accepts an `issue_fetcher`; new
   case seeds `issue_refs=[7]` with a closed snapshot and asserts the second
   tick DISPATCHES the `[merge-conflict]` increment.

## Tasks

- [x] tick.py condition + log line
- [x] class-test case
- [x] ruff + mypy + full suite green

## Done-When

An issue-referenced goal whose issues are all closed and whose PR conflicts
gets its one resolution increment dispatched before any second merge attempt;
the existing heal/park tests stay green.
