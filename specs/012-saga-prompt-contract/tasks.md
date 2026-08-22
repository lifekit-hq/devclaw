---

description: "Task list for spec 012 US1 — increment feed-forward"
---

# Tasks: Saga & Unit-of-Work Prompt Contract — US1 (increment feed-forward)

**Input**: Design documents from `specs/012-saga-prompt-contract/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: REQUIRED (not optional) — the devclaw constitution mandates a named
regression test per behavior-change PR, and zero-token guard tests are
load-bearing. Test tasks are therefore first-class here.

**Organization**: US1 only. US2 (saga authoring slots) and US3 (expected
increment count) are out of this slice per the spec's priority rules.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1
- Paths are repository-root relative, per plan.md's structure decision

---

## Phase 1: Setup

**Purpose**: No project initialization needed — this is a change inside an
existing package with an existing test suite.

- [X] T001 Confirm the worktree's import path resolves to the WORKTREE (not the main checkout): `.venv/bin/python -c "import devclaw; print(devclaw.__file__)"` must print a path under the worktree root (rules/testing.md)
- [X] T002 Capture the green baseline before touching code: `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q` and record the pass count for the PR description

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The read accessor, the size budget, and the shared marker — every
US1 task depends on these three existing before composition can be wired.

**⚠️ CRITICAL**: T003–T005 block all of Phase 3.

- [X] T003 [P] Add `PRIOR_INCREMENTS_KEEP = 6_000` and `PRIOR_INCREMENTS_TRUNCATION_MARKER` (naming the elision and pointing at the full deliveries record) to `devclaw/goal/prompt_budget.py`, following the existing `LOG_KEEP` / `DELIVERIES_KEEP` shape
- [X] T004 [P] Add `PRIOR_INCREMENTS_MARKER` to `devclaw/advance_brief.py` alongside `STEERING_MARKER` / `FAILURE_CONTEXT_MARKER`, with the same never-drift docstring contract (generator + detectors key off this exact string)
- [X] T005 Add read-only `increment_records(goal_id)` to `devclaw/goal/store/content.py` plus `delivery_records` / `settlement_statuses` to `devclaw/goal/state.py` — join deliveries with settlements by `ref_id`, oldest first, no mutation, no transaction (data-model surface 1). *Revised during implementation*: the delivery body turned out to carry the worker's own `Agent summary:` prose and NOT devclaw's settle header, so the terminal status must come from `goal_settlements` and the prose must be dropped (#358 — research R2).

**Checkpoint**: The renderer has a data source, a bound, and a marker to build from.

---

## Phase 3: User Story 1 — An increment knows what its siblings delivered (Priority: P1) 🎯 MVP

**Goal**: Every advance session's prompt states which increment it is and what
the previous increments in the same saga delivered, including each one's
verdict — so work already shipped is not re-implemented.

**Independent Test**: Run a goal through two increments under the stub engine
and inspect the second dispatch's action text: it must name its position and
describe the first increment's outcome and verdict. Testable with no change to
goal authoring or issue grading.

### Tests for User Story 1 (write first; they must FAIL before implementation)

- [X] T006 [P] [US1] Create `tests/test_prior_increments.py` with `test_first_increment_brief_states_no_prior_increments_explicitly` — zero delivery blocks renders the explicit absence statement, never a blank or omitted section (FR-004, acceptance 2)
- [X] T007 [P] [US1] Add `test_second_increment_brief_states_prior_delivery_outcome_and_verdict` to `tests/test_prior_increments.py` — a settled `status=done … sandbox gate=passed … PR <url>` block renders objective + status + verdict + PR in the section (FR-002/FR-003, acceptance 1)
- [X] T008 [P] [US1] Add `test_failed_prior_increment_reported_in_next_brief` to `tests/test_prior_increments.py` — a `status=failed` / `gate=FAILED` block renders its failure verbatim and the section's imperative line warns not to treat it as shipped (FR-005, acceptance 3)
- [X] T009 [P] [US1] Add `test_prior_increments_section_is_bounded_and_elides_loudly` to `tests/test_prior_increments.py` — many blocks tail-keep under `PRIOR_INCREMENTS_KEEP` behind the truncation marker, newest entries surviving (FR-009b, SC-006)
- [X] T010 [P] [US1] Add `test_unreadable_delivery_block_degrades_to_stated_gap` to `tests/test_prior_increments.py` — a malformed block renders the stated-gap line and the renderer never raises (edge case, constitution VI)
- [X] T011 [P] [US1] Add `test_display_goal_annotates_prior_increments_with_their_count` and `test_display_goal_does_not_annotate_a_first_increment` to `tests/test_prior_increments.py` — the annotation names the COUNT and is absent at zero (#547/#550). *Revised during implementation*: annotating mere presence decorated every dispatch identically and polluted `status.next`, which the #550 regression caught.
- [X] T012 [US1] Add `test_advance_brief_carries_prior_increments_after_a_settled_delivery` to `tests/test_goal_tick.py` — seed a goal, settle one advance via `FakeEngine`, tick, and assert the dispatched action's `goal` text contains the section with the prior increment's outcome (the end-to-end US1 regression)
- [X] T013 [US1] Add `test_idle_tick_performs_no_increment_record_read` to `tests/test_goal_tick.py` — on an idle tick the record read is never called (spied) and `FakeClaude.calls == 0` (SC-007, constitution III)

### Implementation for User Story 1

- [X] T014 [US1] Create `devclaw/goal/prior_increments.py` with `IncrementRecord`, `parse_record(instruction, body, status)` and pure `render(records)` — position line, imperative build-only-on-shipped line, per-increment entries (newest last) as `- {objective} → status=… gate=… PR=… error=…`, explicit absence statement at zero records, stated-gap line for unreadable ones, never raises. Only devclaw-generated fields are parsed out of the body; the worker's `Agent summary:` is deliberately dropped (#358).
- [X] T015 [US1] Apply the size bound inside `render` via `prompt_budget.cap_prior_increments` — to the ENTRY LIST only, never the assembled section: the budget tail-keeps and the marker sits at the head, so capping the whole section would eat the marker every detector keys off (#547/#550) along with the framing (FR-009b)
- [X] T016 [US1] Extend `advance_brief.display_goal` in `devclaw/advance_brief.py` to append `+N prior increment(s)` — parsed from the count rendered into the marker line — and ONLY when N ≥ 1, matching the `+N steering line(s)` pattern
- [X] T017 [US1] Add the blank-safe `prior_increments: str = ""` kwarg to `_advance_brief` in `devclaw/goal/tick.py` and render the marked section between the `Done when:` block and the failure-context section — existing call sites and test stubs stay byte-unaffected (cognition-prompts blank-safe rule)
- [X] T018 [US1] Wire composition in `_handle_long_lived_advance` (`devclaw/goal/tick.py`): AFTER the `should_plan` gate returns true, call `store.increment_records(goal_id)`, render, and pass `prior_increments=` into `_advance_brief` — the read must sit below the gate so idle/blocked ticks are byte-identical (research R7); best-effort, a store hiccup degrades to no feed-forward rather than wedging dispatch

**Checkpoint**: US1 is fully functional and independently testable.

---

## Phase 4: Polish & Cross-Cutting Concerns

- [X] T019 [P] Check `docs/flows/task-execution.md` against the new brief composition step; if the diff makes it wrong, fix it and update its currency tag in `docs/INDEX.md` in the SAME PR (CLAUDE.md docs-honesty rule)
- [X] T020 Run the full suite green at or above the T002 baseline: `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q`; confirm every pre-existing zero-token guard test still passes
- [X] T021 Run the `quickstart.md` validation block and confirm each named test listed there exists and passes
- [X] T022 Update `specs/012-saga-prompt-contract/spec.md` Status line to record US1 as implemented (US2/US3 still named-unsized), then open the PR per `.claude/rules/git-workflow.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (T001–T002)**: no dependencies — run first; T001 is load-bearing in a worktree
- **Foundational (T003–T005)**: depends on Setup; BLOCKS Phase 3
- **US1 (T006–T018)**: depends on Foundational
- **Polish (T019–T022)**: depends on US1 complete

### Within User Story 1

- Tests (T006–T013) are written FIRST and must fail before implementation
- T014 (renderer) before T015 (bound applied inside it)
- T016 (display) is independent of T014/T015 — different file
- T017 (brief kwarg) before T018 (composition wiring) — same file, ordered
- T012/T013 pass only after T017 + T018

### Parallel Opportunities

- T003 and T004 are different files — parallel
- T006–T011 are all in the new test file and independent of each other in
  content, but share one file: write them in one pass rather than concurrently
- T014/T015 (renderer) and T016 (display) touch different files — parallel
- T019 (docs) can proceed alongside T020 (suite run)

---

## Implementation Strategy

**MVP = this whole slice.** US1 is the spec's P1 and is independently valuable:
even if US2 and US3 never ship, saga increments stop repeating each other.

1. T001–T002: verify import path, capture baseline
2. T003–T005: foundational (marker, budget, accessor)
3. T006–T013: write the failing regressions
4. T014–T018: implement until they pass
5. T019–T022: docs honesty, full suite, PR

**Out of slice**: US2 (saga authoring slots) and US3 (expected increment count)
stay named-unsized in the spec until US1 has production nights behind it.
Concurrency, declared file scopes, and serial integration belong to spec 010
(FR-014) and must not be touched here.

---

## Notes

- Every task names an exact file path; [P] means different files, no shared edit
- Commit after each logical group; branch per change, PR not push-to-main
- If a zero-token guard test fails, the change is wrong — never the test
