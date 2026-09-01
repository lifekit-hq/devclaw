# Contract-truth hygiene (issues #787 + #788)

## What

Two prompt rules, one per end of the contract pipeline, closing the class
where a false claim about current code inside a contract burns worker
sessions:

- Done-gate (#787): a clause contradicted by a **deliberate** design
  decision is a `needs_human` question, never an `off_track` correction.
- Intake grader (#788): an acceptance clause asserting current-code facts
  is `needs-refinement`, never a contract.

## Context

fs-479 (2026-09-01): the issue's acceptance text falsely claimed the
backend "excludes pending". Cost of that one false clause: 3 worker
sessions, 5 done-gate rounds, a scope-fence violation (the worker changed
the fenced service to satisfy the correction — #358 class), a churn park,
and a human `git revert`. The gate wrote "a design reversal, not a dropped
clause" into its own rationale while still answering `off_track` — the
`needs_human` park path already existed (`tick_donegate.py:639`); the
defect was pure classification. Full post-mortems on the issues.

## Requirements

- Both rules live in the prompts (one imperative statement each, per
  cognition-prompts conventions) — no mechanism changes; the existing
  `needs_human` → `needs_answer` park and the grader's needs-refinement
  labeling are untouched.
- Cognition quality is judged by evals, not stubs: the fs-479 round-2
  shape becomes an evaluator eval fixture expecting `needs_human`.
- A merely-unmet clause still classifies `off_track`; a purely behavioral
  acceptance section still grades exactly as today.

## Plan / Tasks

- [x] `devclaw/prompts/goal-evaluator.md` step 3b: contradicted-by-
  deliberate-design ⇒ `needs_human` with a both-sides question; corrections
  never negate deliberate design or cross `out_of_scope` fences.
- [x] `devclaw/prompts/intake-readiness.md`: verifiable intent = desired
  observable behaviour; a current-implementation assertion in acceptance ⇒
  needs-refinement, quoting the clause + the rewrite rule.
- [x] Eval fixture `tests/cognition/fixtures/evaluator/
  needs_human_design_conflict.json` (production-trace, fs-479 shape) +
  README fixture list.

## Done-When

- Mechanism guards green on the new fixture (loads, prompt round-trips,
  `validate()` reproduces `needs_human`); full suite, ruff, mypy green.
- The live-cognition eval (`DEVCLAW_RUN_COGNITION_EVALS=1`) shows
  `needs_human` on the new fixture when eyeballed.
