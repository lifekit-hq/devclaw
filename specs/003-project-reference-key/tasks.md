# Tasks: Registry as single source of truth for dispatch — P1

**Feature**: `003-project-reference-key` | **Branch**: `feat/project-reference-key`
**Scope**: P1 only, single big-bang PR (clarify Q2). US1 (reference key) + US2
(preflight) + US3 (write-time validation) ship together.

**Ordering principle**: additive capability first (nothing breaks), then the
breaking signature cutover + full migration in one sweep, then regression tests,
then a green full suite. The suite need only be green at the END of the PR, but
Phases 1–3 are individually runnable.

---

## Phase 1: Foundational — additive capability (nothing breaks yet)

- [ ] T001 [P] [US3] Add write-time path validation + normalization to `ProjectRegistry.create()` and `update()` in `devclaw/project_registry.py` — normalize `workspace_dir` via `_normalize_workspace` (`:512`) and reject a non-container-side-shaped path with a reason, mirroring `_validate_sandbox_image` (`:75-82`, called at `:278/:391`). Store the canonical value (today stored raw at `:296/:370`). Existence is NOT checked here (that's preflight).
- [ ] T002 [P] [US1] Add `ProjectRegistry.resolve_dispatch(project_id) -> ResolvedDispatch` (or a `(workspace_dir, repo_url)` tuple) in `devclaw/project_registry.py`, reading the row via `get(project_id)` (`:315`); return None/raise a typed miss on unknown id. Do NOT return the override knobs (they ride the existing path joins — P3).
- [ ] T003 [P] [US2] Add a shared zero-token preflight predicate `workspace_is_dispatchable(workspace_dir) -> str | None` (returns a reason string on failure, None on ok) — checks the path exists AND `(Path/".git")` exists, reusing the predicate from `engine/workspace.py:195` / `tick_guards.py:287`. Put it where both the goal path and direct path can import it (e.g. `engine/workspace.py` or a small `loom` helper). No LLM, no container.
- [ ] T004 [P] Add a test fixture helper `register_tmp_project(registry, workspace_dir, **knobs) -> project_id` in `tests/goal_fakes.py` (mirror the `seed_goal` style) that registers a throwaway project row and returns its `id`, so dispatch tests migrate mechanically.
- [ ] T005 [P] [US3] Named regression test `test_register_project_validates_and_normalizes_workspace_path` in `tests/test_project_registry.py` (quickstart scenario 5) — asserts a non-canonical path is normalized (or a bad one rejected), a well-formed path round-trips.

**Checkpoint**: `pytest tests/test_project_registry.py` green; full suite still green (nothing removed yet).

---

## Phase 2: The cutover — swap signatures, resolve, reject, preflight (BREAKING)

*These land together; after T006–T010 the old call sites won't compile-call, so Phase 3 migration is same-PR.*

- [ ] T006 [US1] In `devclaw/server/tools.py`, swap `workspace_dir: str` → `project_id: str` on `dispatch_task` (`:32`), resolve via `registry.resolve_dispatch` before `queue.submit` (`:83`), and raise `ToolError("unknown project_id: …")` on miss (mirror `project_status` `:1327-1329` / `file_intake` precedent). Keep `queue.submit(workspace_dir=<resolved>, …)`.
- [ ] T007 [US1] Swap `workspace_dir` → `project_id` on `implement_feature` (`:96`), `fix_bug` (`:117`), `review_repository` (`:140`) — they forward through `dispatch_task`, so only pass `project_id` along (single resolution point).
- [ ] T008 [US1] Swap `workspace_dir` → `project_id` on `onboard` (`:213`); resolve before its own `queue.submit` (`:250`).
- [ ] T009 [US1] On `create_goal` (`:724`): swap `workspace_dir` → `project_id`, REMOVE the `repo_url` param, resolve BOTH `workspace_dir` + `repo_url` from the row, forward to `goals.create_goal(workspace_dir=<resolved>, repo_url=<resolved>, …)` (`:770`). Swap `start_program` (`:271`) the same way (`:296`).
- [ ] T010 [US2] Wire preflight at the two admission seams: (a) DIRECT path — call `workspace_is_dispatchable` in `tools.py` after resolution, before `queue.submit`, raising `ToolError` on failure (before `submit`'s synchronous `pump=True` claim); (b) GOAL path — call it in `goal/tick_dispatch.py` just before `prepare_ws` (`~:226`), routing a failure through `_block_on_prep_failure` → `mechanical:prep`. Leave `sandcastle.py:301` as the last-resort backstop.
- [ ] T011 Confirm the dry-run tools (`_dry_goal`, `tools.py:557`, `workspace_dir="/dev/null"`) are untouched — they take no caller `project_id` (harness-internal, clarify Q1). Add a one-line comment marking them as the intentional special-case.

**Checkpoint**: targeted `pytest tests/test_dispatch_task.py tests/test_goal_admission.py` — expected RED until Phase 3 migrates them.

---

## Phase 3: Migrate every in-repo call site + test off `workspace_dir` (same PR)

- [ ] T012 Migrate non-test source call sites: `grep -rnE 'dispatch_task\(|implement_feature\(|fix_bug\(|review_repository\(|onboard\(|create_goal\(|start_program\(' devclaw/ cli.py` and switch each to `project_id=` (register/resolve as needed). Include `cli.py` dispatch paths.
- [ ] T013 [P] Migrate the ~13 direct dispatch test call sites (`grep -rl 'dispatch_task(\|implement_feature(\|fix_bug(\|review_repository(' tests/`) to use `register_tmp_project(...)` → `project_id=`.
- [ ] T014 [P] Migrate goal-creation test call sites (`create_goal(`/`start_program(`) across `tests/` to `project_id=` via the fixture. Preserve every existing assertion (esp. `FakeClaude.calls == 0` guards) unchanged.
- [ ] T015 Sweep the remaining `workspace_dir=` dispatch-param usages flagged by the blast-radius grep (~90 files) — only the ones passing it AS a dispatch/tool param change; fixture/`seed_goal`/internal `workspace_dir` on goal rows stay. Confirm no caller-facing `workspace_dir` remains on the 7 tools (`grep` per quickstart migration check).

**Checkpoint**: `pytest tests/test_dispatch_task.py tests/test_goal_admission.py tests/test_goal_tick.py` green again.

---

## Phase 4: Named regression tests (the P1 acceptance surface)

- [ ] T016 [P] [US1] `test_dispatch_by_project_id_resolves_workspace_and_repo` (quickstart 1) in `tests/test_dispatch_task.py`.
- [ ] T017 [P] [US1] `test_dispatch_unknown_project_id_rejected_zero_token` (quickstart 2) — `ToolError`, no task row, `FakeClaude.calls == 0`, engine never launched.
- [ ] T018 [P] [US2] `test_preflight_rejects_non_git_workspace_before_claim` (quickstart 3) — rejected at admission; task never a claimed `running` row; `sandcastle` never reached.
- [ ] T019 [P] [US1] `test_by_key_dispatch_preserves_override_knobs` (quickstart 4) — automerge/review_gate decisions identical to path-keyed resolution.
- [ ] T020 [P] Zero-token guard `test_dispatch_resolution_and_preflight_stay_zero_token` (quickstart 6) — idle tick + by-key dispatch + rejected id + preflight rejection all keep `FakeClaude.calls == 0`.

---

## Phase 5: Polish & release

- [ ] T021 Run the FULL suite green: `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q` — count no lower than the pre-change baseline. Verify worktree import path first.
- [ ] T022 Docs honesty (Constitution + git-workflow rule): update `CLAUDE.md` (dispatch tools take `project_id`; correct the stale `task_queue.py:1901` anchor → `1945/1958/1967`), `docs/reference/env-vars.md` if touched, and any doc that shows a `workspace_dir` dispatch example; bump the currency tag in `docs/INDEX.md`.
- [ ] T023 PR body: state the exact count of files touched by the migration (Constitution VI — no silent bounded coverage); include the **waiter-prompt lockstep checklist item** below as a required co-release.
- [ ] T024 [NON-CODE, Denys/OpenClaw] Lockstep release step: update the OpenClaw waiter prompt (`dsdevq/lifekit-stack` GitOps) to dispatch by `project_id`, landing in the SAME window as this deploy — else dispatch breaks on the VPS (FR-008a). This is a checklist item on the PR, not a code task in this repo.

---

## Dependencies & parallelism

- Phase 1 (T001–T005) is fully parallel `[P]` and additive — safe to land first.
- Phase 2 (T006–T011) depends on T002 (resolve) + T003 (preflight predicate); T006 before T007 (aliases forward through it).
- Phase 3 depends on Phase 2 (signatures changed) and T004 (fixture); T013/T014 parallel across disjoint files.
- Phase 4 depends on Phase 2+3; all `[P]` (distinct test names/files).
- Phase 5 gates the PR.

## MVP / review note

There is no partial-ship MVP here — clarify Q2 fixed this as one big-bang PR.
The "independently testable" checkpoints above exist for *implementation
sequencing*, not separate delivery. Suite must be green at PR time.

## Format validation

All tasks use `- [ ] TNNN [P?] [USn?] description + file path`. Setup/foundational
and polish tasks carry no story label by design.
