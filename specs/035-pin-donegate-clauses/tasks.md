# Tasks: Pinned done-gate clauses — decompose the contract once per revision

**Input**: Design documents from `/specs/035-pin-donegate-clauses/`

**Prerequisites**: plan.md, spec.md (clarified), research.md, data-model.md, quickstart.md

**Tests**: This feature touches two tripwire classes (fail-closed gates; doctor seeded-faults), so the named tests below are REQUIRED, not optional — and they extend existing class tests, never mint siblings (rules/testing.md).

**Organization**: One phase per user story = one reviewable PR each (plan.md slicing). Each PR runs the full suite + ruff + mypy before `gh pr create`, in a worktree, branch `feat/035-<slice>`.

## Phase 1: Setup

- [X] T001 Create worktree + branch `feat/035-pin-us1` from origin/main per rules/git-workflow.md; verify import path (`python -c "import devclaw; print(devclaw.__file__)"` prints the worktree)

## Phase 2: Foundational (blocking for all stories)

- [X] T002 Add `goal_contract_pins` table DDL + migration in devclaw/goal/state.py (schema per data-model.md; PRIMARY KEY (goal_id, revision))
- [X] T003 [P] Create devclaw/goal/clause_pin.py: `PinnedClause` / `ContractPin` dataclasses, `assign_ids(clauses) -> ContractPin` (mechanical c1..cN, research D2), `carry_forward(old_pin, new_clauses)` (byte-identical text match, sets carried_from; per FR-003), JSON (de)serialization that round-trips data-model.md's clause record
- [X] T004 Create devclaw/goal/state_pins.py: `GoalStatePinsMixin` with `read_pin(goal_id, revision)`, `write_pin(pin)`, `update_pin_accounting(goal_id, revision, clause_updates)` — single-writer seam; register the mixin in `GoalStore` (devclaw/goal/state.py imports/bases, beside GoalStateProblemsMixin)

**Checkpoint**: suite green — new table + module are inert until the gate uses them.

## Phase 3: User Story 1 — the rubric is derived once and reused (P1) 🎯 MVP

**Goal**: round 1 harvests the pin from its own evaluator output; later rounds judge exactly the pinned ids; unknown id fails closed. Ships as PR 1 with the doctor check (FR-008 rides the state-shape change).

**Independent test**: quickstart Scenario 1 + 2 — three rounds, one decomposition, prompt carries ids; `c99` verdict fails closed without a churn charge.

- [X] T005 [US1] Add pinned-clauses mode to devclaw/prompts/goal-evaluator.md: when a `Pinned clauses` block is present, steps 1/1a are replaced by "judge EXACTLY these clauses by id; reference each id in `clauses[]`; never add/remove/merge/split/rename" — one home per rule, imperative, no incident history (rules/cognition-prompts.md); decomposition mode stays byte-identical when the block is absent
- [X] T006 [US1] devclaw/goal/evaluator.py: `build_prompt(..., pinned_clauses=None)` blank-safe kwarg rendering the `Pinned clauses` block (id + verbatim text per line); `_parse_clauses(raw, pinned_ids=None)` — when pinned, every entry must carry a known id, unknown/missing id raises `GoalEvalError` (fail-closed, FR-002)
- [X] T007 [US1] devclaw/goal/tick_donegate.py: around the existing `_live_contract` digest (l.~326) — `read_pin(goal, revision)`; hit ⇒ pass `pinned_clauses` into the evaluator call; miss ⇒ run decomposition mode and harvest: `assign_ids` from the round's parsed clauses + record ceremony drops (FR-005), `write_pin`, log `pinned contract revision <digest> (N clauses)`; corrupt/unreadable pin ⇒ re-decompose with `recovery` reason recorded in the round rationale (FR-006)
- [X] T008 [US1] devclaw/goal/tick_donegate.py: `GoalEvalError` rounds (crash/unparseable/unknown id) do NOT increment `donegate_rounds` (research D4 — move the l.~647 increment behind judgment classification); pin untouched; problems-catalog entry preserved
- [X] T009 [P] [US1] devclaw/doctor/checks_instance.py: `check_contract_pins` — table present post-migration, every row's goal_id resolves, newest-revision row per active goal parses with unique ids; FAIL names row + remedy (FR-008)
- [X] T010 [US1] Tests (tripwire, extend existing classes): in tests/test_goal_tick.py (or the done-gate cases it hosts) — pin-once-per-revision (quickstart S1: FakeClaude returns varying decompositions, count stays fixed, one row); unknown-id fails closed with NO `donegate_rounds` increment (S2); corrupt pin recovers loudly. In tests/test_doctor.py — seeded faults for `check_contract_pins` (S5). Existing zero-token and fail-closed cases pass unmodified (SC-005)
- [X] T011 [US1] Docs in the same PR: docs/flows/task-execution.md done-gate paragraph + docs/INDEX.md currency tag; /ship ritual; PR 1

**Checkpoint**: US1 alone is the MVP — rubric drift dead, everything else unchanged.

## Phase 4: User Story 2 — monotonic, legible accounting (P2)

**Goal**: satisfied set persisted in the pin record; stable denominator; flips require cause. Branch `feat/035-pin-us2` after PR 1 merges.

**Independent test**: quickstart Scenario 3 — progress 1→2 against constant denominator; causeless flip malformed; cited flip accepted.

- [X] T012 [US2] devclaw/goal/evaluator.py + devclaw/prompts/goal-evaluator.md: `flip_cause` contract — a clause entry flipping a previously-satisfied id carries a cited cause (repo change in the span since the satisfying evidence, or a named defect in it); absent ⇒ `GoalEvalError` (FR-011); prompt states the rule once
- [X] T013 [US2] devclaw/goal/tick_donegate.py: after each judgment round, `update_pin_accounting` — satisfied/evidence/satisfied_round per clause id; `donegate_progress` computed as |satisfied| against the pinned denominator (feeds the existing churn brake unchanged); pass the prior satisfied set into `_parse_clauses` as the flip-rule base
- [X] T014 [US2] devclaw/goal/tick_donegate.py: refusal rationales name only pinned ids with verbatim text (FR-004); a clause satisfied via a recorded Decision is counted satisfied with `via_decision` set and the Decision cited as evidence (FR-007 arithmetic, clarify Q4)
- [X] T015 [US2] Tests (extend T010's named cases, parametrize — never siblings): quickstart S3 monotonic accounting + flip rule; decided-clause-counted-satisfied; churn brake fed by stable denominator (a converging goal never parks)
- [X] T016 [US2] /ship ritual; PR 2

## Phase 5: User Story 3 — amendment re-pins once, with carry-forward (P3)

**Goal**: digest change ⇒ exactly one re-decomposition, named in rationale; byte-identical clauses inherit accounting; Decisions survive. Branch `feat/035-pin-us3` after PR 2 merges.

**Independent test**: quickstart Scenario 4.

- [X] T017 [US3] devclaw/goal/tick_donegate.py: digest ≠ pinned revision ⇒ decomposition mode for the new revision; harvest via `clause_pin.carry_forward` (satisfied/evidence/via_decision inherited for byte-identical text, carried_from set); rationale names the revision change as the cause (FR-003)
- [X] T018 [US3] Verify-and-pin Decisions across re-pin: a decided clause present in the amended contract is not re-asked (spec 031 semantics; FR-007) — covered in the evaluator's Decisions block rendering for the new pin
- [X] T019 [US3] Tests (extend the same named cases): quickstart S4 — one new pin row, carry-forward fields, Decision survival, changed clauses start open
- [X] T020 [US3] /ship ritual; PR 3

## Final Phase: Polish & cross-cutting

- [X] T021 Stamp spec status: specs/035-pin-donegate-clauses/spec.md Status → Implemented (all three stories) in PR 3; note the eval-set companion remains open work
- [X] T022 Remove worktrees; verify main green; deploy stays Denys's button — note in the PR 3 body that the pin activates per-goal on the next done-gate round with no migration

## Dependencies

- Phase 2 blocks everything (T002 → T004 sequential: DDL → module → mixin; T003 parallel with T002).
- US1 (Phase 3) blocks US2 (accounting writes into the pin record US1 creates); US2 blocks US3 (carry-forward copies the accounting fields US2 maintains). Strictly serial PRs — this is one seam (`tick_donegate.py`), not a fan-out.
- Within US1: T005/T006 before T007; T008 independent of T005-T007 after T004; T009 [P] anytime after T002.

## Parallel example (US1)

```
After T004: T009 (doctor check) ∥ T005+T006 (prompt/parse) — different files.
T007 waits for T005/T006. T010 last.
```

## Implementation strategy

MVP = Phase 3 (US1 + doctor check): rubric drift — the failing metric's direct cause — dies in PR 1. US2 makes convergence measurable and flip-proof; US3 closes the amendment lane. Whole spec is the commitment; P1 landing is not a stopping point.
