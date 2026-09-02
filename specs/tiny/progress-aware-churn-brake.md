# TinySpec: the churn brake counts flat rounds, not rounds

**Branch**: fix/progress-aware-churn-brake
**Date**: 2026-09-02
**Status**: done
**Complexity**: small

## What

The done-gate churn brake is a pure round counter: `rounds = donegate_rounds + 1`
(`tick_donegate.py:654`), park at `DONEGATE_ROUND_CAP` (3). It has no notion
of progress, so it cannot tell the treadmill it was built for — fresh nits
every round, nothing landing — from a goal that is visibly converging.

Live proof 2026-09-02: `devclaw-030-env-admission` parked `donegate_churn`
at **14 of 15 clauses**, the satisfied count having risen every round, and
needed a human to say "keep going". Two more goals parked the same way that
day (`fs-421`, `fs-429`) with the gate *right* each time and the park
*wrong* each time. Of the eight human pings that day, three were this.

Class fix: persist the best satisfied-clause count a round has reported
(`goal_status.donegate_progress`). A round that beats it restarts the counter
at 1 and stores the new best. Only a **flat** count accumulates toward the
cap. A verdict with no clauses (pre-decomposition) is flat — never progress.

## Context

| File | Role |
|------|------|
| `devclaw/goal/tick_donegate.py` | Will be modified — the rule at the round count; both close paths and the merge-conflict resume reset the new field with `donegate_rounds` |
| `devclaw/goal/models.py` | Will be modified — `GoalStatus.donegate_progress` |
| `devclaw/goal/state.py` · `state_status.py` | Will be modified — column, migration, the four persistence sites |
| `devclaw/goal/service.py` | Will be modified — `steer_goal` / `resume_goal` reset it (a human vouch restores the full budget, same contract as `donegate_rounds`) |
| `devclaw/doctor/checks_instance.py` | Will be modified — `instance.donegate.goal_status_donegate_progress` (spec-016 FR-014: a store-shape change ships its doctor check) |
| `tests/test_goal_tick.py` · `tests/test_doctor.py` | Will be modified — extend the churn class tests; seeded-fault pair |

## Requirements

1. `progress = count(clauses where satisfied)` from the evaluator's verdict.
   `progress > donegate_progress` ⇒ `rounds = 1`, persist the new best.
   Otherwise `rounds = donegate_rounds + 1`. Park iff `rounds >= cap`.
2. The park path, the keep-going path, both ACHIEVE paths, the merge-conflict
   resume, and both human vouch verbs keep the two fields coherent — no site
   resets one without the other.
3. Persisted state shape change ⇒ doctor check + seeded-fault test in the
   same PR. A DB predating the migration reads the column as absent; the
   check names the consequence (a converging goal still parks).
4. The `LEGAL` table and the single-ACHIEVE-emitter structure are untouched.
   The structural guard `test_green_mechanical_verification_alone_never_closes_a_goal`
   pins the ACHIEVE line's literal text; it is updated to the new literal,
   and still asserts exactly two verdict-owned ACHIEVE emitters.
5. Tripwire class (brake machinery): extend the existing churn tests, never
   mint siblings. The new progress test is verified to FAIL under the old
   pure counter.

## Plan

1. Model field + column + migration + four persistence sites.
2. The rule in `tick_donegate.py`; coherent resets at every existing
   `donegate_rounds=0` site (`tick_donegate.py` ×3, `service.py` ×2).
3. Doctor check mirroring `check_goal_status_slice_hold_count`, registered
   beside it; seeded-fault pair mirroring its tests.
4. Tests: a rising count at cap−1 keeps going with `rounds == 1` and the
   best persisted; the park test states its flat-progress precondition
   explicitly; the vouch test asserts both fields reset.

## Tasks

- [x] `GoalStatus.donegate_progress` + column + migration + persistence
- [x] Progress-aware rule; coherent resets at all five sites
- [x] `check_goal_status_donegate_progress` + registration
- [x] `test_donegate_progress_column_absent_detected` / `_present_is_ok`
- [x] `test_done_gate_round_that_beats_the_best_clause_count_restarts_the_churn_counter`
      — verified to fail under the old counter
- [x] Park test gains an explicit flat-progress precondition; vouch test
      asserts both fields reset; structural ACHIEVE guard literal updated
- [x] Full suite (1296) + `ruff` + `mypy` green

## Done When

- [x] All tasks checked off
- [x] A goal whose satisfied-clause count rises across done-gate rounds is
      never parked by the churn brake
- [x] A goal whose count is flat for `DONEGATE_ROUND_CAP` rounds still parks
      — the brake is narrowed, not weakened
- [ ] Measured: pings-per-goal-week attributable to `donegate_churn` drops
      after deploy (the 2026-09-02 baseline is 3 of 8)
