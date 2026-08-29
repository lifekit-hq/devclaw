# Tasks: Saga & Unit-of-Work Prompt Contract — US3

**Input**: [spec.md](./spec.md) (US3), [plan-us3.md](./plan-us3.md)

**Branch**: `feat/012-us3-expected-increment-count` — ONE PR.

> Bookkeeping reconciled 2026-08-29: these boxes were checked retroactively —
> the work shipped but the rows were never ticked (verified: the named tests
> and `expected_increments` plumbing exist on main). Stale unchecked rows here
> fed the dispatch slice-guard false positives (#728).

Tests first (repo rule: every behavior change ships a named regression test,
named after the behavior). `[P]` marks tasks with no shared file.

## Phase 1 — tests (write first, red)

- [X] **T001** `tests/test_intake.py`:
  `test_file_intake_records_the_filers_expected_increment_claim_and_basis`
- [X] **T002** `tests/test_intake.py`:
  `test_file_intake_records_unstated_extent_rather_than_defaulting_to_one`
- [X] **T003** `tests/test_intake.py`:
  `test_file_intake_rejects_an_increment_count_with_no_basis`
- [X] **T004** `tests/test_intake.py`:
  `test_file_intake_rejects_a_non_positive_increment_count`
- [X] **T005** `tests/test_intake.py`:
  `test_parse_expected_increments_reads_back_the_claim_and_absence`
- [X] **T006** [P] `tests/test_intake_readiness.py`:
  `test_grading_records_the_filers_claim_verbatim_when_the_grader_agrees`
  (acceptance scenario 1)
- [X] **T007** [P] `tests/test_intake_readiness.py`:
  `test_grading_preserves_the_claim_and_surfaces_the_disagreement_for_a_human`
  (acceptance scenario 2 / FR-010b)
- [X] **T008** [P] `tests/test_intake_readiness.py`:
  `test_regrading_an_unchanged_work_item_records_an_identical_expected_count`
  (acceptance scenario 3 / SC-005b — the two grades return *different* assessed
  numbers and the recorded count is still identical)
- [X] **T009** [P] `tests/test_intake_readiness.py`:
  `test_work_item_with_no_stated_extent_is_surfaced_for_a_human`
  (FR-011, the hand-written-issue path)
- [X] **T010** [P] `tests/test_intake_readiness.py`:
  `test_grader_that_cannot_assess_the_extent_surfaces_for_a_human_not_agreement`
- [X] **T011** [P] `tests/test_intake_readiness.py`:
  `test_sizing_disagreement_never_flips_the_readiness_verdict`
  (assumption 3 — orthogonal axes)
- [X] **T012** [P] `tests/test_intake_readiness.py`:
  `test_needs_sizing_label_is_removed_when_a_regrade_reaches_agreement`
- [X] **T013** [P] `tests/test_intake_readiness.py`:
  `test_expected_increment_count_never_selects_an_execution_shape`
  (FR-012 — the ready comment states the saga shape; the grade result carries
  no shape selector; the prompt forbids recommending one)
- [X] **T014** [P] `tests/test_intake_readiness.py`:
  `test_sizing_assessment_spends_no_additional_cognition_call` (FR-013)
- [X] **T015** [P] `tests/test_intake_readiness.py`:
  `test_readiness_prompt_carries_the_filers_claim_and_the_sizing_output_field`
  (presence AND absence: the claim block renders the number; with no claim it
  says so, and the raw template does not carry the number)
- [X] **T016** [P] `tests/test_intake_readiness.py`:
  `test_grade_backlog_reports_the_issues_that_need_a_human_size_decision`

## Phase 2 — implementation (green)

- [X] **T017** `devclaw/intake_readiness.py`: `SizingAssessment` dataclass,
  `ReadinessVerdict.sizing`, `_increment_claim_block`, `build_prompt` +
  `evaluate` kwargs, defensive `increments` parsing in `validate`.
- [X] **T018** `devclaw/prompts/intake-readiness.md`: the claim input block, the
  sizing assessment rules (assess in units of work; never restate the claim as
  your own number; `null` when unsure; size never affects `ready`; do not
  recommend an execution shape), the extended JSON schema.
- [X] **T019** `devclaw/intake.py`: `NEEDS_SIZING_LABEL`,
  `MIN_INCREMENT_BASIS_CHARS`, claim validation in `validate_shape`, the body
  section in `issue_body`, `parse_expected_increments`, `sizing_outcome`,
  the sizing paragraph + fixed-shape line in `_readiness_comment`,
  `_apply_sizing_label`, `grade_and_label` returning a dict, `regrade` passing
  the parsed claim through and merging the result, `grade_backlog`'s
  `needs_sizing` bucket.
- [X] **T020** `devclaw/server/tools.py`: `file_intake` gains
  `expected_increments` / `increment_basis` and threads them into the scheduled
  grade; the three tool docstrings state the new axis.

## Phase 3 — docs & gates

- [X] **T021** `docs/reference/intake-shape.md`: two new rows in the Fields
  table + an "Expected increments" section (claim is the filer's, grading
  validates, disagreement surfaces, shape is always a saga).
- [X] **T022** `docs/flows/autonomous-issue-pipeline.md`: the grade stage
  records two axes and can emit `needs-sizing`.
- [X] **T023** `docs/INDEX.md`: currency tags for both changed docs.
- [X] **T024** `ruff check .` clean; full suite at or above the 2075-passed
  baseline; PR referencing #600.

## Out of slice (named, not built)

- Feeding the recorded count into the saga/unit-of-work prompt so it actually
  sizes the task graph — US2 / spec 010 territory (plan-us3 assumption 5).
- Any execution-shape branch keyed on the count — rejected by FR-012.
- Backfilling a claim onto already-filed issues — a human amends the issue and
  re-grades, the same loop spec 006 FR-010 established.
