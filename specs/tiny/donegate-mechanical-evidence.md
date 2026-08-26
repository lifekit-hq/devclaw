# TinySpec: done-gate flips ground in mechanical evidence and say why

**Branch**: fix/donegate-mechanical-evidence
**Date**: 2026-08-26
**Status**: done
**Complexity**: small

## What

Two defects in the done-gate's mechanical post-checks, both observed on
`devclaw-auth-ping-path-2026-08-25` round 1 (2026-08-25 23:28): the evaluator
returned `achieved` with all clauses satisfied, but `_test_clause_existence_only`
flipped a clause on the words "test exists" — even though the increment's
verify gate had executed the full suite green in-sandbox — and the resulting
`off_track` kept the model's achieved-sounding rationale, so the log
contradicted itself. Cost: one full no-op dispatch round (worker session +
gate re-run) whose only output was rewording evidence. Second misfire of this
regex's class in five days (see the lkd-honest-widgets note at
`_EXISTENCE_EVIDENCE_RE`).

## Context

| File | Role |
|------|------|
| `devclaw/goal/evaluator.py` | Will be modified — `validate()` gains `verified_execution`; flip suppressed on mechanical run evidence; downgrade branches rewrite the rationale |
| `devclaw/goal/prior_increments.py` | Context — `_GATE_RE` is the precedent for reading the host-written `Verify gate …: PASSED` line out of a delivery body |
| `devclaw/goal/engine.py` | Context — `_task_detail` (line ~507) is the single writer of that line, from the runner's structured verify payload |
| `tests/test_goal_evaluator.py` | Will be modified — named regression tests for both behaviors |

## Requirements

1. `validate()` accepts `verified_execution: bool = False`. When True, a
   satisfied test clause is NOT flipped by `_test_clause_existence_only` —
   mechanical run evidence outranks wording. When False, behavior is
   byte-identical to today (every existing call site/test unaffected).
2. `evaluate()` derives `verified_execution` from the `deliveries` text it
   already receives: the LAST host-written `Verify gate \`…\`: PASSED|FAILED`
   marker (same shape as `prior_increments._GATE_RE`; last marker wins so an
   old green increment never vouches for a newer failed one). No marker →
   False. The helper is pure and never raises.
3. Every at-done-gate `achieved → off_track` mechanical downgrade (no-clauses
   branch, unsatisfied-after-normalization branch, structural-axis branch)
   records a rationale that STATES the downgrade and its reason; the model's
   original rationale may follow for context but never stands alone as the
   recorded rationale.
4. Named regression tests:
   - `test_existence_only_flip_yields_to_mechanical_verify_evidence` —
     achieved + existence-wording clause + `verified_execution=True` stays
     `achieved`.
   - `test_existence_only_flip_still_applies_without_run_evidence` —
     same clause with `verified_execution=False` (default) still flips
     (pins requirement 1's back-compat).
   - `test_verified_execution_derived_from_last_gate_marker` — the deliveries
     parser: PASSED-last → True; FAILED-last → False; no marker → False.
   - `test_mechanical_downgrade_rationale_states_the_flip` — a downgraded
     verdict's rationale contains the downgrade statement, not the bare
     model rationale.

## Plan

1. `evaluator.py`: add module-level `_VERIFY_GATE_RE` + pure helper
   `_deliveries_verified_execution(deliveries: str) -> bool`.
2. Thread `verified_execution` through `validate()` (blank-safe default) and
   guard the existence-only flip with `and not verified_execution`.
3. Rewrite the rationale in the three downgrade branches to lead with the
   mechanical reason (append the model rationale as context).
4. `evaluate()`: compute the flag from `deliveries` and pass it.
5. Tests per requirement 4; full suite + ruff + mypy.

## Tasks

- [x] `_VERIFY_GATE_RE` + `_deliveries_verified_execution` helper
- [x] `validate(verified_execution=False)` param + flip guard
- [x] Downgrade branches lead with the mechanical reason
- [x] `evaluate()` derives and passes the flag
- [x] Four named regression tests
- [x] Full suite + `ruff check .` + `mypy` green

## Done When

- [x] All tasks checked off
- [x] A done-gate judgment over deliveries whose last verify-gate marker is
      PASSED can no longer be flipped to off_track on evidence wording alone
- [x] Every mechanical downgrade's recorded rationale names the downgrade
- [x] Existing evaluator tests pass unchanged (back-compat default)
