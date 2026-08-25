# Tasks: Goal-as-Pointer

**Input**: Design documents from `/specs/019-goal-as-pointer/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/create-goal.md

**Tests**: REQUIRED — named regression test per behavior (constitution);
the refusal matrix and the #684-class freshness fixture are the load-bearing
coverage.

**Organization**: one phase per user story; each story lands as ONE
reviewable PR; the whole spec is the commitment.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [X] T001 Record the green suite baseline (full `pytest -q` count) and verify `ruff check .` + `mypy` clean, per .claude/rules/testing.md

---

## Phase 2: Foundational

**Purpose**: the one reference seam every story consumes

- [X] T002 Create devclaw/goal/issue_ref.py: `IssueSnapshot`, the `IssueFetcher` protocol with the gh-backed default (`gh api repos/{owner}/{repo}/issues/{n}`), readiness read reusing the grading pipeline's ready-label constant, and mechanical `extract_acceptance(body)` per the spec 015 section convention (research D2/D4)
- [X] T003 [P] Add `DEVCLAW_GOAL_TEXT_BUDGET` (default 1000) through the single doorway in devclaw/config.py (research D3)
- [X] T004 [P] Add `FakeIssueFetcher` (call-counting, scriptable per-ref responses) in tests/goal_fakes.py, following the FakeClaude/FakeEngine conventions
- [X] T005 [P] Unit tests for the seam in tests/test_goal_issue_refs.py: test_extract_acceptance_slices_section, test_extract_acceptance_returns_none_when_absent, test_fetcher_protocol_injectable

**Checkpoint**: seam importable and tested; no behavior change yet

---

## Phase 3: User Story 1 — First-class refs, fetched fresh at dispatch (Priority: P1) 🎯 MVP

**Goal**: `issue_refs` on the goal; dispatch-time fetch into the brief;
closed/unfetchable semantics; zero idle fetches.

**Independent Test**: create → edit issue → dispatch carries edited body;
close issue → next dispatch skips with a loud log; fetch error → human-gated
block; idle ticks make zero fetcher calls.

- [X] T006 [US1] Add `issue_refs: list[int] = []` to the Goal dataclass + goal.yaml round-trip in devclaw/goal/models.py (additive, defaulted — existing yaml loads unchanged)
- [X] T007 [US1] Accept `issues` on the MCP surface in devclaw/server/tools/goals.py and thread it to GoalService.create_goal in devclaw/goal/service.py with structural validation only in this story: exists, same-project repo, no duplicates (budget/readiness/exclusivity arrive in US3/US4) — refusal messages per contracts/create-goal.md
- [X] T008 [US1] Dispatch boundary in devclaw/goal/tick_dispatch.py: for the next referenced item, fetch via the injected seam; open ⇒ render an issue section into the advance brief from the snapshot; closed ⇒ skip + loud goal-log line + advance; fetch error ⇒ block with the existing lost-ref human-gated semantics (research D7)
- [X] T009 [US1] Render refs and lane in get_goal display (devclaw/goal/service.py or its display helper) per contracts/create-goal.md
- [X] T010 [P] [US1] Named tests in tests/test_issue_ref_freshness.py: test_dispatch_brief_carries_post_edit_issue_body, test_closed_issue_item_skips_loudly_without_worker_session, test_unfetchable_ref_blocks_human_gated, test_ordered_refs_advance_in_order
- [X] T011 [P] [US1] Extend the idle guard in tests/test_goal_tick.py: test_idle_tick_makes_no_issue_fetches (FakeIssueFetcher.calls == 0 alongside FakeClaude.calls == 0)
- [X] T012 [US1] Doctor project check: referenced-goal records parse and refs are well-formed ints, in devclaw/doctor/checks_project.py, with a seeded-fault test (spec 016 FR-014)

**Checkpoint**: pointer goals dispatch fresh — shippable alone (essays still
possible; freshness already real)

---

## Phase 4: User Story 2 — done_when defaults to live acceptance scenarios (Priority: P2)

**Goal**: defaulted contract = scenarios read live at each done-gate round;
explicit done_when overrides; absence blocks loudly.

**Independent Test**: referenced goal without done_when is judged against
the issue's scenarios; a mid-goal scenario edit is honored next round;
scenario-absence blocks the round, never evaluates empty.

- [X] T013 [US2] Creation-time guard in devclaw/goal/service.py: refs + defaulted done_when requires every ref to carry an acceptance section (extract_acceptance non-None), else refuse naming the section convention OR the explicit-done_when alternative (US2 sc.4)
- [X] T014 [US2] Done-gate call site (devclaw/goal/tick_donegate.py / tick_settle.py): when done_when is defaulted, fetch scenarios live via the seam and thread them as the contract into the evaluator call; fetch failure or absent section ⇒ block the round legibly — LOAD-BEARING, not best-effort (research D5)
- [X] T015 [US2] Document the load-bearing-vs-best-effort collector distinction in .claude/rules/cognition-prompts.md in the same increment (plan's constitution note)
- [X] T016 [P] [US2] Named tests in tests/test_done_when_scenarios.py: test_defaulted_contract_reads_scenarios_live_at_eval, test_mid_goal_scenario_edit_honored_next_round, test_explicit_done_when_overrides_scenarios, test_scenario_absence_blocks_round_never_evaluates_empty, test_creation_refuses_default_when_section_missing

**Checkpoint**: one contract source end to end

---

## Phase 5: User Story 3 — The length budget (Priority: P2)

**Goal**: over-budget referenced goals refused at the doorway with the
actionable relocation message.

**Independent Test**: over-budget referenced goal refused (message names
budget, count, destination issue, regrade flow); within-budget accepted;
issue-less goal of any length unaffected.

- [X] T017 [US3] Enforce the budget in GoalService.create_goal (devclaw/goal/service.py) for referenced goals only — objective + note counted, explicit done_when excluded (research D3); refusal message per contracts/create-goal.md
- [X] T018 [P] [US3] Named tests in tests/test_goal_issue_refs.py: test_over_budget_referenced_goal_refused_with_relocation_message, test_within_budget_accepted, test_issue_less_goal_exempt_from_budget, test_budget_configurable_via_config_doorway

**Checkpoint**: essays with refs impossible

---

## Phase 6: User Story 4 — Readiness-gated references (Priority: P2)

**Goal**: only ready-graded issues referencable; readiness re-checked at
dispatch; exclusivity (one issue → one live goal).

**Independent Test**: needs-refinement ref refused naming the grading verb;
graded ready → accepted; label revoked mid-goal → item skips; second live
goal on the same issue refused naming the holder.

- [ ] T019 [US4] Readiness check at creation in devclaw/goal/service.py via the seam's label read — refusal names the grading flow (grade_backlog / regrade_intake)
- [ ] T020 [US4] Re-check readiness at the dispatch boundary in devclaw/goal/tick_dispatch.py — revoked label ⇒ skip + loud log, consistent with US1's closed semantics (US4 sc.3)
- [ ] T021 [US4] Exclusivity at creation: scan live goals' issue_refs in the same project, refuse overlap naming the holding goal id (research D6)
- [ ] T022 [P] [US4] Named tests in tests/test_goal_issue_refs.py: test_unready_ref_refused_naming_grading_verb, test_ready_ref_accepted_after_grading, test_ready_revoked_mid_goal_skips_item, test_second_live_goal_on_same_issue_refused_naming_holder, test_done_goal_releases_its_issue

**Checkpoint**: the relocation half enforced — grooming replaces authoring

---

## Phase 7: User Story 5 — The issue-less lane stays open (Priority: P3)

**Goal**: no-refs goals behave exactly as today, pinned.

- [ ] T023 [P] [US5] Regression-pin the issue-less lane in tests/test_goal_issue_refs.py: test_issue_less_goal_creation_byte_compatible (no budget, no readiness, today's firming path), test_lane_visible_in_get_goal_output

**Checkpoint**: all five stories functional

---

## Phase 8: Polish & Cross-Cutting

- [ ] T024 [P] Docs honesty: DEVCLAW_GOAL_TEXT_BUDGET in docs/reference/env-vars.md; create_goal tool docstring updated in devclaw/server/tools/goals.py; sweep docs describing goal authoring (docs/architecture.md if it names the goal contract shape); bump touched docs' currency tags in docs/INDEX.md
- [ ] T025 Run specs/019-goal-as-pointer/quickstart.md end to end: full suite vs T001 baseline, ruff, mypy, doctor, and the live smoke against a dev instance with a sandbox repo
- [ ] T026 Wire the refusal-message contract test: every refusal row of contracts/create-goal.md asserted for rule + input + fixing verb in tests/test_goal_issue_refs.py (SC-006's machine half)

---

## Dependencies & Execution Order

- **Phase 1 → 2**: sequential; Phase 2's T003/T004/T005 parallel after T002.
- **US1 (Phase 3)**: after Phase 2 — the MVP; everything else layers on its
  refs field and dispatch seam.
- **US2, US3, US4 (Phases 4–6)**: each depends on US1's plumbing, mutually
  independent — sequenced as listed only for one-PR-per-story review; US3
  and US4 both edit service.py's validation chain, so they rebase in order.
- **US5 (Phase 7)**: anytime after US1; last because it is pinning, not
  building.
- **Polish (Phase 8)**: after all stories.

### Parallel Opportunities

[P]-marked test tasks run beside their story's implementation (disjoint
files). Cross-story parallelism is deliberately unused — one worker, one
reviewable PR per story, per repo convention.

## Implementation Strategy

MVP = Phases 1–3 (US1): structured refs + dispatch freshness kills the
stale-contract class on its own. Then US2 (one contract source) → US3
(budget) → US4 (readiness + exclusivity) → US5 (pin) — one PR each,
validating each checkpoint's independent test. The whole spec is the
commitment; any dropped story is said out loud.

## Implementation notes (US1, 2026-08-25)

- T002 landed WITHOUT `extract_acceptance` — the scenario extractor is US2's
  consumer and ships with it; building it consumer-less here would be dead
  code behind a green checkbox.
- T003 (`DEVCLAW_GOAL_TEXT_BUDGET`) deliberately NOT built yet: its only
  consumer is US3's enforcement — it moves into that increment.
- T007's "exists" check at creation is deferred into US4's readiness gate:
  both need the same fetch at the doorway, and creation runs on the sync MCP
  path where the subprocess seam needs the design US4 carries. Until then a
  nonexistent ref surfaces at the first dispatch as the human-gated
  `lost_ref` block — loud, never silent.
- The all-refs-closed path proposes done via the existing `_open_done_gate`
  (zero worker sessions, grounded close) rather than a new mechanism —
  `BLOCKED→OPEN_DONE_GATE` is already legal, so a steered-while-parked goal
  takes the same path.
- Fetch failures block with `blocked_kind="lost_ref"` (the existing
  human-gated lost-reference class) rather than a new kind.

## Implementation notes (US2, 2026-08-25)

- The doorway check landed as `GoalService.create_goal_async` (the MCP tool
  now awaits it): the async fetch seam the sync create path cannot carry.
  The sync `create_goal` stays fetch-free for internal/test callers; the
  gate's load-bearing block is the backstop for anything that bypasses the
  doorway. This also delivers US1's deferred existence-at-creation check for
  the defaulted-contract path (the fetch doubles as the existence probe).
- Admission gains `has_issue_refs`: a referenced goal's defaulted contract
  satisfies the "something to grade against" condition — the doorway proved
  the sections exist before admission runs.
- T015's rule-doc update (load-bearing vs best-effort collectors) ships in
  this increment.
