---

description: "Task list for spec 012 US2 — saga authoring slots"
---

# Tasks: Saga & Unit-of-Work Prompt Contract — US2 (saga authoring slots)

**Input**: Design documents from `specs/012-saga-prompt-contract/` —
`spec.md` (US2 story + FR-007/008/009/009a/009b) and `plan-us2.md`.

**Tests**: REQUIRED. The constitution mandates a named regression test per
behavior-change PR; zero-token guard tests are load-bearing.

**Organization**: US2 only. US1 is merged; US3 (expected increment count, the
intake/grading surface) is a different arc and no file it owns is touched.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependencies)
- Paths are repository-root relative

---

## Phase 1: Setup

- [X] T001 Work in a git worktree on `feat/012-us2-saga-authoring-slots`; confirm `.venv/bin/python -c "import devclaw; print(devclaw.__file__)"` prints the WORKTREE path (rules/testing.md)
- [X] T002 Capture the green baseline before touching code: `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q` → **2075 passed, 4 skipped**

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the size bound and the shared `Goal:` marker must exist before the
renderer can be written against them.

- [X] T003 [P] Add `SAGA_SLOT_KEEP = 1_200`, `SAGA_SLOT_TRUNCATION_MARKER` and `cap_saga_slot()` to `devclaw/goal/prompt_budget.py`. Unlike every existing cap this one is **head-keep**: an authored declarative slot's contract is at the top, whereas a delivery/log tail is the current state. Document that inversion in the module docstring, which currently asserts "every cap TAIL-KEEPS".
- [X] T004 [P] Promote the two-sided `Goal:` line prefix to a shared constant `GOAL_LINE_PREFIX` in `devclaw/advance_brief.py` and make `objective_from_brief` key off it, so the generator moving into a new module cannot drift from the detector (#547/#550).
- [X] T005 Add the three slots to `Goal` in `devclaw/goal/models.py` as `Optional[list[str]] = None`, documenting that `None` means "authored before the schema" and `[]` means "explicitly declared empty" — the distinction the whole backward-compatibility story rests on.

**Checkpoint**: the renderer has a bound, a marker, and a typed carrier.

---

## Phase 3: User Story 2 — a saga is authored against a schema (P2)

**Goal**: five named slots, a rejection that names an unfilled one, and one
fixed-structure bounded framing generator.

**Independent Test**: create a saga with a slot omitted and confirm the
rejection names it; create two sagas with different content and confirm their
briefs have identical section structure.

### Tests first

- [X] T006 [P] `tests/test_saga_framing.py::test_saga_framing_renders_every_slot_in_a_fixed_order_with_its_imperative`
- [X] T007 [P] `tests/test_saga_framing.py::test_explicitly_empty_slot_states_its_absence_instead_of_being_omitted`
- [X] T008 [P] `tests/test_saga_framing.py::test_two_sagas_with_different_content_render_identical_section_structure` (SC-003)
- [X] T009 [P] `tests/test_saga_framing.py::test_goal_authored_before_the_slot_schema_renders_todays_framing_byte_identical` (the backward-compat regression)
- [X] T010 [P] `tests/test_saga_framing.py::test_oversized_slot_content_is_bounded_and_says_so` + `test_whole_saga_framing_stays_under_the_declared_bound_for_adversarial_input` (FR-009b)
- [X] T011 [P] `tests/test_goal_admission.py::test_create_goal_rejects_an_unfilled_saga_slot_naming_it` and `::test_explicitly_empty_saga_slots_are_admitted`
- [X] T012 [P] `tests/test_goal_tick.py::test_advance_brief_carries_the_authored_saga_slots` and `::test_advance_brief_for_a_pre_schema_goal_is_unchanged_by_the_slot_schema`
- [X] T013 [P] `tests/test_elicitation.py::test_grill_done_step_passes_through_the_saga_slots_when_present` and `::test_grill_done_step_without_slots_is_byte_unaffected`

### Implementation

- [X] T014 New `devclaw/goal/saga_framing.py` — pure, never-raises `render(goal) -> str`. Emits `Goal:` / `Done when:` exactly as today, then the three slot sections when they are not `None`. Structurally mirrors `prior_increments.py`.
- [X] T015 `devclaw/goal/tick.py::_advance_brief` delegates the framing block to `saga_framing.render(goal)`; output for a pre-schema goal must be byte-identical.
- [X] T016 `devclaw/goal/admission.py` — three `reject`-severity checks (`missing_out_of_scope`, `missing_invariants`, `missing_established`), each naming the slot and saying that an empty list is the way to declare it empty. Wired into `verify_goal()` in the existing ordering.
- [X] T017 `devclaw/goal/store/base.py` — write the three keys in `create_goal`; read them in `load_goal` so an ABSENT key stays `None` (never coerced to `[]`).
- [X] T018 `devclaw/goal/service.py` — thread the slots through `create_goal` and `verify_goal`; surface them on `get_goal` so a slot an operator cannot see cannot be verified.
- [X] T019 `devclaw/server/tools.py` — the three params on the `create_goal` and `verify_goal` tools, documented (omit ⇒ rejection; `[]` ⇒ declared empty); `start_program` passes explicitly-empty slots as the FR-012b operator-present class.
- [X] T020 `devclaw/goal/self_issue.py` — the unattended self-fix pickup fills REAL slots (no sprawl beyond the issue; suite + documented invariants survive; the issue's diagnosis is accepted, do not re-triage).
- [X] T021 `devclaw/elicitation.py` + `devclaw/prompts/scope-grill.md` — the grill's `done` action carries the three slot lists; optional, so an older caller is byte-unaffected.

**Checkpoint**: US2 delivers independently — authoring is schema'd, rejection
names the slot, and the framing is bounded.

---

## Phase 4: Polish

- [X] T022 Docs honesty sweep: `docs/reference/mcp-tools.md`, `docs/flows/task-execution.md`, `docs/architecture.md` — fix anything the diff makes wrong, and bump the currency tag in `docs/INDEX.md` for every doc changed.
- [X] T023 `ruff check .` clean; full suite at or above the T002 baseline.
