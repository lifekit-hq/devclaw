---
description: "Task list for spec 032 — Verification ownership"
---

# Tasks: Verification ownership

**Input**: Design documents from `/specs/032-verification-ownership/`
**Prerequisites**: plan.md, spec.md (clarified 2026-09-03), research.md (R1–R8), data-model.md, contracts/, quickstart.md
**Tests**: only tripwire classes (zero-token, fail-closed gates, CAS/single-writer, sandbox fence, brakes, materialize span, doctor seeded-faults, structural guards). Every test task below extends a named existing class test; no sibling files are minted. Ordinary behavior ships without a test.
**Organization**: one phase per increment, each landing as ONE reviewable PR, in plan.md's order: US1 → US2 → US3 → US5 → doctrine (+ US4 manifest surface). Line anchors are into `main` = c77d8ce and drift as earlier phases land; re-anchor by symbol.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on an unfinished task)
- **[Story]**: US1 / US2 / US3 / US4 / US5 from spec.md
- Paths are repository-relative.

## Path Conventions

Single project: `devclaw/` (host), `runner/` (sandbox worker), `tests/` (stubbed suite), `docs/`, `.specify/memory/constitution.md`.

---

## Phase 1: Setup

**Purpose**: nothing to scaffold — every change lands in an existing module. One guard so later phases cannot lose anchors.

- [x] T001 Verify the worktree imports itself (`.venv/bin/python -c "import devclaw; print(devclaw.__file__)"` prints the worktree path) and the baseline is green: `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q` + `ruff check .` + `mypy`; record the passing count in specs/032-verification-ownership/tasks.md (this line) for the per-PR "not below baseline" rule. **Baseline 2026-09-03: 1342 passed, 5 skipped; after Phase 3: 1348 passed, 5 skipped.**

---

## Phase 2: Foundational

**Purpose**: the one shared prerequisite every phase's `gh` reads depend on.

- [x] T002 Add a 20 s `asyncio.wait_for` around the subprocess in `_gh` (devclaw/goal/remote_checks.py:158), `_run_gh` (devclaw/goal/mergeability.py:46) and `_run_gh` (devclaw/goal/merge_on_close.py:58); a timeout returns `(-1, "timeout after 20s")` — never raises, never hangs the tick (research R1, spec edge "CI provider unreachable"). Extend `tests/test_mergeability.py::test_probe_never_raises_when_gh_is_missing` with a hung-`gh` case (parametrize: missing binary / sleeps past the timeout) asserting `None` and no exception.

**Checkpoint**: every host `gh` read is bounded; phases 3–7 can start.

---

## Phase 3: User Story 1 — the project's CI verdict is a fact the loop requires (Priority: P1) 🎯 MVP

**Goal**: the CI rollup for the exact PR head is read *before* the done-check review is dispatched; red never spends cognition, pending holds at zero tokens, merge requires the same green head (research R1–R2, contract ci-rollup-and-scorecard.md, data-model §1–2).

**Independent Test**: `FakeRemoteChecker` returning `failing` on a done proposal ⇒ no `review_repository` dispatch, `FakeClaude.calls == 0`, an `auto-ci` steering row naming the check; `pending` ⇒ `blocked_kind == "mechanical:ci"` and `pending_done_proposal == 1`; flip to `passing` ⇒ review dispatched; a moved head at merge ⇒ no `_attempt_merge`.

### Implementation for User Story 1

- [x] T003 [US1] In devclaw/goal/remote_checks.py extend `RemoteChecksResult` (`:80`) with `head_sha: str = ""`, `failing_names: tuple[str, ...] = ()`, `pending_names: tuple[str, ...] = ()`; fold `none` into `no_workflows`; delete `blocks_done` (`:87`), `CI_GATE_MODE` (`:66`) and `check_branch` (`:180`); remove `REMOTE_CHECKS_ENABLED`'s sibling `DEVCLAW_GOAL_CI_GATE` read from devclaw/config.py (~`:211`).
- [x] T004 [US1] In devclaw/goal/remote_checks.py add `async def check_pr(pr_url: str) -> RemoteChecksResult`: `gh pr view <url> --json headRefOid,baseRefName,statusCheckRollup`, then `gh api repos/<o>/<r>/branches/<base>/protection/required_status_checks/contexts` (404 ⇒ every context required); extend `combine_states` (`:110`) with the required-name filter and the name tuples; `default_checker()` (`:213`) returns `check_pr`; `RemoteChecker` (`:59`) becomes `Callable[[str], Awaitable[RemoteChecksResult]]`; update `devclaw/goal/tick_context.py:256` accordingly.
- [x] T005 [P] [US1] Add `pending_done_proposal INTEGER NOT NULL DEFAULT 0` and `ci_green_head TEXT NOT NULL DEFAULT ''` to `goal_status`: DDL + `ALTER TABLE` migration in devclaw/goal/state.py (beside `envcap_redispatches` at `:120` / `:313`), upsert/load in devclaw/goal/state_status.py (`:115`, `:139`, `:171`, `:206`, `:344`), dict projection in devclaw/goal/store/status.py (`:345`), fields on `GoalStatus` in devclaw/goal/models.py (beside `:305-310`) with `mechanical:ci` added to the `blocked_kind` taxonomy comment (`:215-223`).
- [x] T006 [US1] In devclaw/goal/tick_donegate.py move the rollup read to the top of `_open_done_gate` (`:728`, before `prepare_ws` at `:757`): `passing` ⇒ `ci_green_head=head_sha` and proceed; `failing` ⇒ `store.append_steering(goal_id, [f"[remote-checks] {names} failing on {head_sha[:7]} — fix or quarantine, never bypass"], source="auto-ci")` + `Event.RESUME_IDLE` (no `donegate_rounds` increment) + `Outcome.SLEPT`; `pending`/`unknown` ⇒ `Event.BLOCK` with `blocked_kind="mechanical:ci"`, `blocked_on` naming `pending_names`, `pending_done_proposal=True`; `no_workflows` ⇒ `Event.BLOCK` `blocked_kind="mechanical:env"`, `blocked_on="no CI definition on the default branch — onboarding writes it"`; `infra_broken` ⇒ typed Problem via `devclaw/goal/problems.py::new_problem` (`kind="env"`, `raised_by="done_gate"`, options CORRECT / a new `DECLARE_IN_SCOPE` / CANCEL, default CORRECT, timebox 0) inside the same `store.transaction()` as the BLOCK. Delete the post-evaluator block at `:433-493`.
- [x] T007 [US1] In devclaw/goal/tick_donegate.py re-read `check_pr` immediately before `_attempt_merge` in `_resolve_done_gate` (`:508`) and in `_finalize_pending_merge` (`:830`): merge only when `state == "passing"` and `head_sha == status.ci_green_head`; otherwise `Event.BLOCK` `mechanical:ci` with `pending_done_proposal=True` (a moved head re-opens the gate on the new head). Clear both new columns on `Event.ACHIEVE` (`:584-589`, `:841-846`).
- [x] T008 [US1] In devclaw/goal/tick.py add the `pending_done_proposal` fast path in `_handle_long_lived_advance` right after the pending-merge finalizer (`:686-691`, before the project hold at `:709`): call `_open_done_gate` directly with `finished_detail=""`; add `_autoheal_ci` in the blocked branch beside `_autoheal_env_cap` (`:383`): gated by `next_heal_at` (reuse `heal_attempts` backoff from `tick_guards.py:267-300`), re-read `check_pr` on `status.pending_merge_pr or the goal PR url`, `passing` ⇒ `_heal_unblock` (proposal stays pending), `failing` ⇒ steering + unblock, else stay; zero LLM on every branch. Clear both columns in the productive-settle reset block at devclaw/goal/tick_settle.py:282-290 and on cancel in devclaw/goal/service.py (the `cancel_goal` transition).
- [x] T009 [P] [US1] Doctor: two checks via `_goal_status_column_finding` (devclaw/doctor/checks_instance.py:600) — ids `instance.goal_status.pending_done_proposal_column` and `instance.goal_status.ci_green_head_column`; register in `INSTANCE_CHECKS` (`:787`).
- [x] T010 [US1] Tests (tripwire: zero-token + fail-closed + brake): in tests/test_goal_tick.py rewrite the remote-checks class for the new position — `FakeRemoteChecker` (`:1865`) takes `pr_url`; replace `test_failing_remote_checks_block_the_close` (`:1896`) with `test_red_rollup_on_a_done_proposal_spends_zero_cognition_and_steers_the_failing_check`; replace `test_never_ran_ci_blocks_the_close_under_strict_gate` (`:1921`) with `test_no_ci_definition_holds_the_goal_as_an_environment_gap`; replace `test_broken_ci_infra_closes_with_annotation_under_flexible_gate` (`:1942`) with `test_infra_broken_ci_raises_a_typed_problem_not_a_close`; keep `test_passing_remote_checks_let_the_goal_close` (`:1965`) asserting `ci_green_head` is set; replace `test_unknown_remote_state_fails_open_but_logs` (`:1981`) with `test_unknown_rollup_holds_and_never_approves`; add `test_pending_rollup_holds_on_mechanical_ci_at_zero_tokens_until_green` (hold → heal → review dispatched); add `mechanical:ci` to `test_blocked_kind_stamped_per_block_site` (`:2232`) and `test_blocked_kind_cleared_on_unblock` (`:2266`). In tests/test_merge_on_close.py add `test_a_head_moved_after_the_green_read_never_merges` beside `:72`. In tests/test_doctor.py add seeded `DROP COLUMN`-shaped faults for both columns following `test_missing_goal_convergence_table_detected` (`:389`).
- [x] T011 [US1] Docs: remove `DEVCLAW_GOAL_CI_GATE` from docs/reference/env-vars.md; update docs/flows/task-execution.md's done-gate hop and docs/architecture.md's done-gate paragraph to state the rollup read precedes the review dispatch and the merge requires the green head; bump both currency tags in docs/INDEX.md.

**Checkpoint**: PR 1 — a red or pending CI can no longer consume a done-gate round, a review sandbox or a merge. Full suite green, `ruff`, `mypy`.

---

## Phase 4: User Story 2 — the worker has a legitimate outlet for an environment gap (Priority: P1)

**Goal**: `BLOCKED: env — <item>` is a typed result that holds the whole project on `mechanical:env`, records one catalog row, self-files devclaw work, heals when the environment changes, and never retries (research R3, R6, contract worker-result-line.md, data-model §3, §6).

**Independent Test**: fake agent `script_blocked_env` ⇒ task failed with the env marker, no retry, one `block/env_deficiency` row, project holds with one ping; rewriting the instance `env_ref` ⇒ hold heals on the next tick with no verb; `FakeClaude.calls == 0` throughout.

### Implementation for User Story 2

- [x] T012 [P] [US2] In runner/runner.py add `_classify_block(reason) -> tuple[str, str]` (`^(env|environment)\s*[—:–-]\s*(.+)$`, case-insensitive ⇒ `("env", item)`, else `("contract", "")`) beside `_parse_blocked_reason` (`:884`); stamp `block_kind`/`block_item` into `blocked_payload` (`:1821-1841`); document both forms in `_RETURN_CONTRACT` (`:191`) exactly as contract worker-result-line.md shows; mention the contract in devclaw/engine/__init__.py's `blocked` comment (`:79`).
- [x] T013 [US2] In devclaw/queue/settle.py: new marker `_WORKER_ENV_MARKER = "worker reported environment deficiency:"` (+ public alias) beside `:114-117`; at `:1236` branch on `result.get("block_kind") == "env"` ⇒ `last_failure = f"{_WORKER_ENV_MARKER} {item}"` and `self._store.record_problem(category="block", kind="env_deficiency", message=item, recovered=False, goal_id=…, task_id=…)`; at `:1360` treat the env marker like the blocked marker (fail closed, no retry) with `mark_failed` text naming the item and "devclaw work, filed automatically".
- [x] T014 [US2] In devclaw/env_cap.py add worker-reported rows: `WORKER_PREFIX = "worker:"`, `def instance_env_ref() -> str` (`f"{SANDBOX_IMAGE}|{DEVCLAW_GIT_SHA}|{sha256(devclaw.json at base)}"` — reuse `devclaw/project_manifest.py:256 load_manifest_at_base` for the hash), `def record_worker_deficiency(store, project_id, item, *, goal_id, task_id) -> CapTarget` writing `env_cap_probe:worker:<slug>@<project>` with `status=red`, `evidence`, `remedy`, `env_ref`; make `read_result` (`:138`) report a `worker:*` row green when its `env_ref != instance_env_ref()`; make `red_caps_for` (`:391`) include the project's `worker:*` rows regardless of `declared`; make `refresh_needed` (`:373`) skip them; add `CAP_CI_DEFINITION = "ci:definition"` with a probe (`gh api repos/<o>/<r>/contents/.github/workflows?ref=<default>` length ≥ 1, `unknown` on read error) implicitly declared for every registered project in `_declared_caps_for` (devclaw/goal/tick_guards.py:321) and in the sweep's `_want_caps` (devclaw/goal/tick.py:1174-1184).
- [x] T015 [US2] In devclaw/goal/tick_settle.py fork the worker-block branch (`:352`): when `poll.detail` carries the env marker, call a new `_block_on_env_deficiency(goal_id, status, item, ctx)` in devclaw/goal/tick_guards.py that records the worker row (T014) and delegates to `_block_on_env_cap` (`:357`) so the kind, ping marker and heal are shared; the contract-block path (`needs_answer` Problem) is unchanged.
- [x] T016 [P] [US2] In runner/skills/onboard/00-onboard.md add the CI workflow (`.github/workflows/verify.yml` running the manifest's `verifyCmd` on push/PR) as a sibling artifact of `.devcontainer/Dockerfile` (Q3, research R6); keep the file ≤ 1 page of new text.
- [x] T017 [US2] Tests (tripwire: sandbox fence / retry brake / admission brake): tests/test_runner_blocked.py — extend `test_blocked_payload_emits_structured_result` (`:100`) by parametrizing over the two forms and asserting `block_kind`/`block_item`; tests/acp_fake_agent.py — add `script_blocked_env` emitting `STATUS: BLOCKED: env — dotnet-ef not available` and register it in the script table (`:16`); tests/test_runner_acp.py — parametrize `test_blocked_selfreport_short_circuits_before_verify` (`:88`) over both scripts; tests/test_task_retry.py — parametrize `test_worker_blocked_status_is_not_retried_and_surfaces_reason` (`:218`) over both kinds asserting the env kind records exactly one `block/env_deficiency` problem row; tests/test_env_cap_admission.py — add `test_a_worker_reported_deficiency_holds_every_goal_on_the_project_with_one_ping` and `test_a_worker_reported_deficiency_heals_when_the_instance_env_ref_changes` beside `:195`, plus `ci:definition` in the parametrized `:259` case; assert `FakeClaude.calls == 0` in every new case.
- [x] T018 [US2] Docs: docs/reference/env-vars.md notes `DEVCLAW_SELF_REPO` must be set for env deficiencies to self-file (currently unset on the live instance — an instance-config prerequisite); docs/flows/task-execution.md documents the two blocked forms; docs/reference/devclaw-manifest.md notes `ci:definition` is implicit; INDEX currency tags.

**Checkpoint**: PR 2 — a worker that hits its environment stops, the project holds once, devclaw gets the ticket, and the product repo is untouched.

---

## Phase 5: User Story 3 — gate-input edits are classified and never count as evidence (Priority: P2)

**Goal**: every changed path carries a class computed once in `task_change.py`; one always-hard gate fails gate-input edits and binaries in both modes; the done-gate never sees them as evidence; the worker skills lose the bypass license (research R4, contract change-set-paths.md, data-model §4).

**Independent Test**: a span touching `AGENTS.md` and a `.so` ⇒ the task fails naming both in both dial positions, no retry, nothing delivered; an issue text declaring `` `.github/workflows/*` `` ⇒ the workflow edit classifies `product` and delivers.

### Implementation for User Story 3

- [ ] T019 [US3] In devclaw/task_change.py add `ChangedPath` (fields per contract), `ChangeSet.paths: tuple[ChangedPath, ...] = ()` with `gate_input_paths` / `binary_paths` / `env_decl_paths` properties, `GATE_INPUT_GLOBS`, `ENV_DECL_GLOBS`, `INSTALL_SCRIPT_KEYS = ("preinstall","postinstall","prepare")`, pure `classify_path(path, *, hunk, in_scope) -> str` using `devclaw/loom/diff_paths.py:66 path_in_scope`, and `in_scope_from_text(text) -> tuple[str, ...]` (backticked tokens matching a gate-input glob).
- [ ] T020 [US3] In devclaw/queue/settle.py `_capture_change` (`:186-251`) run `git diff --numstat -M` and `--name-status -M` over `base..head`, build `paths` with `classify_path` (hunk from the existing diff text; `in_scope` from `in_scope_from_text(goal)` — thread the brief text into `_capture_change` via the `change_fn` lambda at `:1296`); replace the binary counting in `_diff_stats` (`:253-267`) with `len(change.binary_paths)`.
- [ ] T021 [US3] Add `_ChangeClassGate` (`gate_id="change_class"`) to devclaw/quality/task_gates.py after `_MaterializeGate` (`:213`), failing on `gate_input_paths` or `binary_paths` with the exact texts in contract change-set-paths.md and a `_CHANGE_CLASS_MARKER` prefix; add `"change_class"` to `ALWAYS_HARD` in devclaw/quality/gate_policy.py:39; insert the gate after `_MaterializeGate` in both chains in devclaw/queue/settle.py (`:1289-1302` and the salvage twin `:1160-1171`); treat `_CHANGE_CLASS_MARKER` as fast-fail no-retry beside `_PROMPT_TOO_LONG_MARKER` (`:1512`); on `env_decl_paths` append the goal-log line `env_declaration_changed: <paths>`.
- [ ] T022 [P] [US3] Evidence rule: add one line to `_done_gate_review_brief` (devclaw/goal/tick_donegate.py:67) and one rule to step 2 of devclaw/prompts/goal-evaluator.md — "AGENTS.md, CI configuration, test-runner configuration, install scripts and binaries are never evidence for a clause"; `load_prompt` placeholders unchanged.
- [ ] T023 [P] [US3] Skills (one home, runner/skills/): rewrite runner/skills/_writes-code/50-repo-gate-conflict.md around the two typed hand-backs (a repo mechanism vs the ticket ⇒ `STATUS: BLOCKED: <the conflict>`; your environment lacks something ⇒ `STATUS: BLOCKED: env — <item>`), deleting the `--no-verify` / `SKIP=` sentence and the "document WHY in AGENTS.md" step; in runner/skills/_writes-code/20-verify-gate-coverage.md:9 point the verify declaration at `devclaw.json` `verifyCmd` and never at a CI workflow; in runner/skills/_writes-code/90-commit.md:23 state that gate inputs and binaries fail the task.
- [ ] T024 [US3] Tests (tripwire: fail-closed gate / materialize span / structural guard): tests/test_materialize_gate.py — add `test_a_gate_input_edit_or_a_binary_fails_the_task_closed_in_both_dial_positions_without_retry` and `test_an_issue_declared_gate_input_path_classifies_as_product` beside `test_the_span_gate_is_always_hard_in_both_dial_positions` (`:73`), and extend `test_every_read_the_change_gate_sees_the_materialized_span` (`:162`) to assert `paths` is computed once; tests/test_gate_policy.py — extend `test_always_hard_gates_block_in_both_modes` (`:22`) with `change_class`; tests/test_runner_skills.py — add `test_the_skill_bundle_licenses_no_gate_bypass` asserting no `--no-verify`/`SKIP=` in any file under runner/skills/; tests/test_goal_tick.py — extend `test_done_gate_review_brief_forbids_existence_only_test_evidence` (`:2013`) with the gate-input rule (presence in the brief, absence of the header leak).
- [ ] T025 [US3] Docs: docs/architecture.md invariant "one definition of the change" gains the class sentence; docs/flows/task-execution.md lists the gate in the chain; INDEX currency tags.

**Checkpoint**: PR 3 — sandbox lore can no longer enter a product repository through a worker.

---

## Phase 6: User Story 5 — the loop measures its own dependence on the human (Priority: P3)

**Goal**: `interventions.per_achieved_goal` on the scorecard, computed from the store only (research R5, contract ci-rollup-and-scorecard.md §scorecard, data-model §5).

**Independent Test**: two achieved `goal_convergence` rows, one steer, one non-worker commit ⇒ `per_achieved_goal == 1.0`, items itemized.

### Implementation for User Story 5

- [ ] T026 [US5] Add `goal_interventions(id, goal_id, verb, ref, made_at)` DDL + indexes in devclaw/goal/state.py (beside `goal_decisions` at `:265`), a `state_interventions.py` mixin (`record_intervention`, `interventions_since(ms)`) following devclaw/goal/state_problems.py, and the facade in devclaw/goal/store/content.py.
- [ ] T027 [US5] Record interventions in devclaw/goal/service.py: `steer_goal` (`:1552`, verb `steer`, ref = steering row id), `resume_goal` (`:1628`, verb `resume`, ref = goal_log line id — still no steering row), `resolve_problem` (`:1473`, verb from the call, ref = decision id).
- [ ] T028 [US5] In devclaw/delivery/__init__.py beside `_recent_commit_subjects` (`:432`) add `_recent_commit_authors(workspace_dir, base) -> list[tuple[sha, email]]` and, in `deliver_change` after the push, record `verb="commit"` for each author email ≠ `git_identity_env()["GIT_AUTHOR_EMAIL"]` (devclaw/git_identity.py:42) — best-effort, never fails delivery.
- [ ] T029 [P] [US5] In devclaw/telemetry.py `compute_scorecard` (`:341`) add the `interventions` block exactly per the contract (denominator from `goal_convergence` achieved rows in the window, `note` on `OperationalError`); render it in `format_scorecard` (`:958`); the CLI `scorecard` subcommand (devclaw/cli.py:230) prints it.
- [ ] T030 [P] [US5] Doctor: `check_goal_interventions_table` (id `instance.scorecard.goal_interventions`) following `check_goal_convergence_table` (devclaw/doctor/checks_instance.py:495); register in `INSTANCE_CHECKS`; tests/test_doctor.py seeded `DROP TABLE goal_interventions` fault following `:389`.
- [ ] T031 [US5] Docs: docs/reference/ scorecard doc (or the observability tools doc) gains the block; INDEX currency tag.

**Checkpoint**: PR 4 — "works without me" is a number.

---

## Phase 7: Doctrine + the US4 manifest surface

**Goal**: the constitution, CLAUDE.md and the architecture doc say what the code now does; the instance fixes this spec subsumes are retired; the manifest can carry an `environment` declaration so declarations accumulate before US4's provisioning plan (FR-013/014, research R7–R8, data-model §7).

**Independent Test**: `tests/test_harness_docs_map.py` green; `parse_manifest` raises `ManifestError` on a malformed `environment` block and returns `environment=None` when absent.

- [ ] T032 Amend .specify/memory/constitution.md Principle V per research R7 (verdict of record = the project's own verification environment read as a mechanical fact for the exact head; the validation lane is the backstop; the human is not a stage); bump to 2.6.0 with the amendment note in Governance.
- [ ] T033 [P] Drop "post-merge human review is the backstop" from CLAUDE.md (`:132-133`) and docs/architecture.md (`:205`), replacing with the Principle V wording; add the `change_class` gate and the `mechanical:ci` hold to CLAUDE.md's hardening list; rewrite the .sandcastle/Dockerfile comment at `:121` as a declared-environment note; add a status line to specs/030-env-admission/spec.md recording that 032 generalizes its capability set.
- [ ] T034 [P] [US4] Manifest surface only: add the `environment` block (`image`, `services[]`, `tools[]`, `registries[]`) to docs/reference/devclaw-manifest.schema.json and docs/reference/devclaw-manifest.md; parse it in devclaw/project_manifest.py (`Manifest.environment: Optional[EnvironmentDecl] = None`, `_parse_environment` following `_parse_validation` at `:178`, absent ⇒ None, malformed ⇒ `ManifestError`); also add `validation` to the schema (existing drift found in research). Extend the existing manifest class test in tests/ (the `parse_manifest` loud-on-malformed case) with one `environment` row — a structural guard, not an instance test.
- [ ] T035 Update docs/INDEX.md currency tags for every doc touched in phases 3–7; run `/docs-audit` scope-limited to those files.

**Checkpoint**: PR 5 — doctrine and code agree; US4 is explicitly deferred with its declaration surface in place.

---

## Dependencies & Execution Order

- Phase 2 (T002) precedes every `gh` read change (T004, T007, T008, T014).
- **US1** (Phase 3) is the MVP and has no dependency on later phases; T005 and T009 can run in parallel with T003–T004; T006–T008 depend on T003–T005.
- **US2** (Phase 4) depends on T005's column reset points only through T008's settle reset (shared line); otherwise independent. T012 and T016 are parallel with T013–T015.
- **US3** (Phase 5) is independent of US1/US2; T022 and T023 are parallel with T019–T021.
- **US5** (Phase 6) is independent; T029 and T030 are parallel with T026–T028.
- Phase 7 depends on all previous phases having landed (it documents them).
- US4 provisioning is NOT in this task list (Q1 = C); only T034's declaration surface is.

## Parallel Execution Examples

- Phase 3: `T005` (schema) ∥ `T009` (doctor) ∥ `T003→T004` (reader), then `T006→T007→T008`, then `T010`, `T011`.
- Phase 4: `T012` (runner) ∥ `T016` (onboard skill) ∥ `T013→T014→T015`, then `T017`, `T018`.
- Phase 5: `T022` (prompts) ∥ `T023` (skills) ∥ `T019→T020→T021`, then `T024`, `T025`.
- Phase 6: `T029` ∥ `T030` ∥ `T026→T027→T028`, then `T031`.

## Implementation Strategy

1. **MVP = Phase 3 (US1)**: the cheapest fact with the largest effect; ends red-CI churn and makes the migration class unmergeable. Ship, deploy, watch one night of cycle reports.
2. Phase 4 (US2) next: the outlet without which US3's gate would just relocate improvisation into the code.
3. Phase 5 (US3): the structural guard.
4. Phase 6 (US5): the number.
5. Phase 7: doctrine and the deferred story's surface.
Each phase: full suite ≥ baseline, `ruff check .`, `mypy`, the `/ship` ritual, one PR, squash merge. Re-anchor line numbers by symbol after each merge.
