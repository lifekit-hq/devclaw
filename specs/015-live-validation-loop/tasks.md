# Tasks: Live-Validation Loop

**Input**: Design documents from `/specs/015-live-validation-loop/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/validation-contract.md

**Tests**: Included (named-regression-test rule; FR-011 makes it explicit).

**Organization**: PR-A = US1 alone (branch `015-acceptance-upstream` off
`origin/main` — no doorway dependency). PR-B = US2+US3 on
`015-live-validation-loop`, stacked on `014-issue-doorway` (#677) — retarget
to main after #677 merges. The whole spec is the commitment.

## Phase 1: Setup

*(no scaffolding — existing package; no tasks)*

## Phase 2: Foundational

*(none — each story's prerequisites are inside its own phase; the spec-014
doorway is already on the base branch)*

## Phase 3: User Story 1 — every spec ships executable acceptance tests (P1) 🎯 MVP

**Goal**: the three upstream/downstream seams require executable acceptance coverage.

**Independent Test**: grade an issue whose criteria are not e2e-expressible → not ready; the FR-002 executed-run requirement is pinned by a named test.

- [X] T001 [US1] Tighten grounding element (c) in `devclaw/prompts/intake-readiness.md`: verifiable intent = checkable by an executable test at the feature's outermost surface (browser e2e / HTTP against the running service / observation of the running scheduler); an outcome only a human walkthrough could check ⇒ not ready, named in `missing`
- [X] T002 [P] [US1] Add the executable-scenario requirement to `.specify/templates/spec-template.md`'s acceptance-scenario guidance (each scenario expressible as an executable test at the outermost surface)
- [X] T003 [P] [US1] Extend the structural-axis enumeration in `devclaw/prompts/goal-evaluator.md` (FR-003): spec acceptance scenarios with no covering executable test are named in `structural_concerns`
- [X] T004 [US1] Named tests in `tests/test_acceptance_executability.py`: prompt-content presence AND raw-template absence for both prompt edits (cognition-prompts rule); FakeClaude not-ready verdict with the executability reason lands `needs-refinement` via `grade_and_label` (SC-005); FR-002 pin — a frontend diff with no executed browser report yields `never_ran` and blocks/advises per the existing dial (delegating to existing browser-gate fixtures)
- [X] T005 [US1] PR-A: run full gate, open PR from `015-acceptance-upstream` (cherry-picked US1 commits) against main

**Checkpoint**: US1 shippable alone

## Phase 4: User Story 2 — a validation run proves the running product (P2)

**Goal**: `validate_product` boots the declared contract, runs suites, files findings through the doorway.

**Independent Test**: stubbed run with a seeded failing scenario → exactly one schema-conformant finding, no PR, no commit, no gate verdict.

- [X] T006 [US2] `devclaw/project_manifest.py`: `ValidationContract` dataclass + `validation` nested-key parsing in `parse_manifest` (fail-loud on malformed; absent ⇒ None) + `resolve_validation_contract(workspace_dir)` reading the merged base
- [X] T007 [US2] Add `"validate_product"` to `TaskKind` in `devclaw/state_store/rows.py`; thread through `devclaw/engine/__init__.py` (`EngineRequest.validation` optional field), `engine/sandcastle.py::_build_payload` and `engine/host.py` (payload key `validation` when present); expose the kind in `devclaw/server/tools/tasks.py::dispatch_task`'s Literal (manual companion trigger)
- [X] T008 [US2] `runner/runner.py`: agent-less `validate_product` branch — no ACP agent, no skills; run `boot` then `suites` (bounded, verify-style env), read the Playwright JSON report, extract failing test titles recursively (`outcome == "unexpected"`), emit `validation_report` per data-model.md (incl. green-by-vacuity note and partial-coverage marker); add the kind to `_KNOWN_KINDS`
- [X] T009 [US2] `devclaw/goal/validation.py` (NEW, layer 2): `findings_from_report(report, contract_present)` → list of `MachineFinding` per the data-model mapping table; `file_validation_findings(store, repo_slug, report)` calling `issue_doorway.file_finding`; run-record log-line composer
- [X] T010 [US2] `devclaw/queue/settle.py`: `validate_product` settle branch — no materialize/change-attachment, no delivery, no review/browser gates; restore workspace (`git reset --hard` + `git clean -fd`); hand `validation_report` to the goal layer for filing + run-record logging; boot/contract failure settles the task failed with the actionable reason (FR-006) while the finding still files
- [X] T011 [P] [US2] Runner tests in `tests/test_validate_product.py`: boot→suites happy path, failing-title extraction, boot-failure short-circuit, missing-report degradation, no agent spawned (fake-agent regression style), green-by-vacuity note
- [X] T012 [P] [US2] Settle tests in `tests/test_validate_product_settle.py`: one schema-conformant finding per failure through a fake gh (parses via `parse_machine_issue`), dedup on second run (014 semantics), no PR/no commit/no gate verdict, workspace restored, green run files nothing and logs a run record, missing contract → failed + `validator|missing-contract` finding

**Checkpoint**: the mechanism works end-to-end in the stub environment

## Phase 5: User Story 3 — companion-first triggering (P3)

**Goal**: per-repo QA goal; post-deploy trigger on; periodic cadence OFF; prod smoke read-only.

**Independent Test**: stubbed deploy completion enqueues one run; schedule OFF ⇒ zero cognition on idle ticks.

- [X] T013 [US3] `devclaw/goal/models.py`: `GoalMode` gains `"qa"`; `QA_DONE_WHEN` standing-contract constant; admission accepts qa mode with the standing done_when (adjust `devclaw/goal/admission.py` if the standing check is mode-gated)
- [X] T014 [US3] `devclaw/goal/tick.py` (+ `tick_dispatch`/`tick_settle` as needed): qa goals never plan/advance and settled validation tasks append run-record log lines instead of opening the done-gate; qa goals are excluded from the single-writer project-hold derivation and their dispatches are not blocked by it; armed `cadence` + due + inside run window ⇒ enqueue one validation run (mechanical); empty cadence (default) ⇒ nothing, zero cognition
- [X] T015 [US3] `devclaw/goal/validation.py`: `trigger_validation(service/store, registry, project_id)` — find the project's qa goal (none ⇒ no-op), resolve the contract from the merged base (missing ⇒ file `validator|missing-contract` + loud log), submit ONE `validate_product` task attached to the qa goal; plus `prod_smoke(url, smoke_path)` — read-only GET, non-2xx/3xx or unreachable ⇒ `deploy_smoke` finding via the doorway
- [X] T016 [US3] Wire the two deploy edges: `devclaw/server/tools/delivery.py::deploy_project` calls the layer-2 verb after a successful deploy (trigger + smoke); `devclaw/goal/tick_donegate.py::_auto_deploy` does the same at its existing log-and-return seam; expose qa mode in `create_goal` (`devclaw/server/tools/goals.py` Literal + `GoalService` pass-through)
- [X] T017 [US3] Named tests in `tests/test_qa_goal.py`: deploy completion → exactly one run + one smoke (SC-004); no qa goal ⇒ nothing; idle qa tick with cadence unset ⇒ `FakeClaude.calls == 0` AND no dispatch (SC-003); armed cadence enqueues inside the window, not outside, and disarm stops; qa goal never plans and never opens the done-gate; qa goal never holds the project single-writer slot; smoke failure files `deploy_smoke|<slug>|<path>` (parses against 014)

**Checkpoint**: the loop is live, human-caused by construction

## Phase 6: Polish & Cross-Cutting

- [X] T018 Docs honesty: `docs/architecture.md` (validation loop paragraph beside the doorway one), `docs/reference/env-vars.md` if any env var was added (aim: none), `docs/INDEX.md` currency tags; note the qa mode in `CLAUDE.md`'s layer-2 line only if it makes the line wrong
- [X] T019 Spec bookkeeping: flip spec Status → Implemented; full gate; open PR-B (US2+US3, base `014-issue-doorway`, `Closes #667`), noting the retarget-after-#677 procedure in the PR body

## Dependencies & Execution Order

- US1 independent (PR-A off main). US2: T006→T007→T008→T009→T010→tests. US3 after US2: T013→T014→T015→T016→T017. Polish last.

## Implementation Strategy

Both PRs land in this arc; the whole spec is the commitment. US1 is the MVP
stop-point only in the formal sense — the loop (US2+US3) is why the spec exists.

## Implementation notes (2026-08-24)

- The mechanical host half landed as the ROOT module `devclaw/validation_loop.py`
  (not `devclaw/goal/validation.py` as planned): the settle path is layer 4 and
  must not import layer 2, and root modules (`task_change.py`,
  `issue_doorway.py`) are the established shared substrate. The layer-2 half —
  `trigger_validation` — landed as a `GoalService` method reusing
  `tick.validation_action` + `_dispatch_action`, so in-flight bookkeeping and
  settle polling ride the existing machinery.
- The auto-deploy-on-achieved edge deliberately does NOT trigger validation:
  US3's contract is "every validation run is human-caused by construction",
  and auto-deploy is not the owner's button-press. Only the `deploy_project`
  MCP tool triggers (and runs the prod smoke).
- `GoalMode` gained `"qa"` (with the store's mode round-trip fixed — it used
  to coerce unknown modes to long_lived, which would have silently turned a
  validation owner into a feature planner); qa goals are excluded from the
  single-writer hold via `scope_key -> None` and from the no-progress
  watchdog; blank cadence is legal ONLY for qa mode (= disarmed, the shipped
  default; the `create_goal` "1d" default sentinel disarms — arm with an
  explicit cadence like `24h`).
- US1 (the upstream/downstream acceptance-test seams) shipped separately as
  PR #680; FR-002 required no browser-gate change (its executed-run law
  already matched) — a named test pins it.
