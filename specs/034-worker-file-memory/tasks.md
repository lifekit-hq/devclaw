# Tasks: Worker file memory — the repo carries the mind, the prompt carries the task

**Input**: Design documents from `/specs/034-worker-file-memory/` (plan.md, research.md, data-model.md, contracts/memory-layout.md, quickstart.md)

**Tests**: tripwire classes only (constitution, Development Workflow): doctor seeded-fault, the legacy-shape instance check, one structural pointer guard, the skill-bundle cap/absence assertions. Ordinary behavior ships no test; the removed lane's tests are removed (symmetric ratchet).

**Organization**: one PR (plan.md "Slicing decision"). Stories are still listed separately so each is independently checkable.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [x] T001 Verify worktree branch `feat/034-worker-file-memory` and that `import devclaw` resolves to the worktree path

## Phase 2: Foundational

- [x] T002 [US1] Replace `merge_repo_notes` / `render_brief_prefix` / `MAX_BRIEF_CHARS` / `scope_key_for` in `devclaw/goal/repo_brief.py` with `worker_memory_pointer(workspace_dir) -> str` (pure fs probe of `<workspace>/.devclaw/MEMORY.md`, never raises, "" when absent); rewrite the module docstring

## Phase 3: User Story 1 — the worker reads memory from the repo (P1)

**Goal**: no repo-notes block in the brief; a one-line pointer when `.devclaw/MEMORY.md` exists; byte-identical otherwise.

**Independent test**: `tests/test_repo_brief.py` pointer guard (present iff file exists; `None`/missing ⇒ "").

- [x] T003 [US1] `devclaw/goal/tick_dispatch.py`: brief prefix = `architecture_map_pointer(checkout) + worker_memory_pointer(checkout)`; delete the `scope` / `store.read_repo_brief` read and rewrite the comment block
- [x] T004 [US1] `runner/skills/_common.md`: add the read half of the protocol (read `.devclaw/MEMORY.md` at session start, pull `memory/*.md` on demand; absence is normal)
- [x] T005 [US1] `tests/test_repo_brief.py`: remove the US2 merge/cap tests; add `test_memory_pointer_present_iff_index_exists` (+ None/missing workspace ⇒ "")

## Phase 4: User Story 2 — the worker maintains memory; the hand-back lane retires (P2)

**Goal**: write policy as an instruction; every host/runner hop of the REPO NOTES lane deleted; `project_docs` dropped at boot.

**Independent test**: runner ACP tests pass with no `repo_notes` field; doctor dropped-shapes test FAILs on a lingering `project_docs`.

- [x] T006 [US2] NEW `runner/skills/_writes-code/06-repo-memory.md`: the write policy (one fact per file; update over append; delete wrong; never goal-scoped state; mechanize first; commit with the increment)
- [x] T007 [US2] `runner/runner.py`: drop the `REPO NOTES:` line from `_RETURN_CONTRACT`; delete `_REPO_NOTES_LINE_RE`, `_parse_repo_notes`, and the `repo_notes` keys in the blocked/result payloads; fix the `runner/acp_client.py` comment
- [x] T008 [US2] `devclaw/goal/models.py` + `devclaw/goal/engine.py`: delete `PollResult.repo_notes` and `_repo_notes()`
- [x] T009 [US2] `devclaw/goal/tick_settle.py`: delete the repo-notes writeback block and the `_repo_brief` import if unused
- [x] T010 [US2] `devclaw/goal/store/content.py` + `devclaw/goal/state_content.py`: delete `write_repo_brief` / `read_repo_brief` / `PROJECT_DOC_KINDS` / `write_project_doc` / `read_project_doc`; update module docstrings
- [x] T011 [US2] `devclaw/goal/state.py`: replace the `project_docs` CREATE with `DROP TABLE IF EXISTS project_docs` (comment names spec 034 + the hard cut); update the module docstring line listing the mixin's tables
- [x] T012 [US2] `devclaw/doctor/checks_instance.py::check_legacy_dropped_shapes`: report `project_docs` (FAIL if present, remedy restart) as `instance.legacy.project_docs_table`; extend `tests/test_doctor.py::test_dropped_shapes_still_present_detected`
- [x] T013 [US2] Runner test fixtures: `tests/acp_fake_agent.py` REPO NOTES lines out; `tests/test_runner_acp.py` `repo_notes` assert out (assert the key is absent); `tests/test_acp_client.py` assert re-pointed at the hand-back's first line
- [x] T014 [US2] `tests/test_runner_skills.py`: `test_wrap_goal_appends_return_contract_on_skills_path` asserts `REPO NOTES` absent; the writes-code lean test asserts the write policy is present and its cap is lifted 13 200 → 13 600 with the spec-034 note

## Phase 5: User Story 3 — one standardized task prompt + onboard seeding (P3)

**Goal**: the advance brief carries no speckit procedure text (one pointer line); onboard/migrate PR seeds `.devclaw/MEMORY.md`; doctor advises on memory health.

**Independent test**: doctor seeded-fault test for `project.worker_memory.health`; `test_goal_tick.py` advance-brief tests stay green (first line unchanged).

- [x] T015 [US3] `devclaw/goal/tick.py::_advance_brief`: keep the `ADVANCE_BRIEF_MARKER` first line; replace the three procedure paragraphs with one pointer sentence; update the docstring
- [x] T016 [US3] `runner/skills/_writes-code/05-speckit-memory.md`: add the one clause the brief carried (a feature missing plan.md/tasks.md gets the speckit steps first); compress wording to fund T006
- [x] T017 [US3] `devclaw/speckit_setup.py`: `WORKER_MEMORY_INDEX` seed text + `ensure_worker_memory_seeded(workspace_dir) -> str | None`; call it from `install_speckit_pr` and `migrate_manifest_pr` next to `ensure_goal_checkouts_ignored`
- [x] T018 [US3] `devclaw/project_manifest.py`: `BOILERPLATE_REVISION = 3` (comment: revision 3 = the `.devclaw/` seed)
- [x] T019 [US3] `devclaw/doctor/checks_project.py`: `check_worker_memory` (advisory; contract in data-model.md) registered in `PROJECT_CHECKS` after `check_goal_checkouts_ignored`; `tests/test_doctor.py::test_worker_memory_drift_warns_with_curate_remedy` seeded-fault test
- [x] T020 [US3] `devclaw/engine/workspace.py`: fix the stale `.devclaw/trends.md` comment

## Phase 6: Polish & docs honesty

- [x] T021 Docs: `docs/architecture.md` (where state lives), `docs/reference/env-vars.md` (DEVCLAW_DB row), `docs/runbooks/doctor.md` (project check list), `ARCHITECTURE.md` (drop `goal_docs` + `project_docs` rows), `CLAUDE.md` (single-writer bullet), `docs/INDEX.md` currency tags
- [x] T022 Spec status header → Implemented 2026-09-07 (one PR); `ruff check .`, `mypy`, `lint-imports`, full suite green

## Dependencies

T002 → T003/T005; T007 → T008 → T009 → T010 → T011 → T012 (each deletes a consumer of the previous); T016 alongside T006 (bundle size); T017 → T018 → T019; T021/T022 last.

## Parallel opportunities

T004/T006/T016 (skills) are independent of the Python deletions; T017–T019 are independent of T007–T013.
