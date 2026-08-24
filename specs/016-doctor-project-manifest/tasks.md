# Tasks: Instance Doctor + Per-Project Manifest

**Input**: Design documents from `/specs/016-doctor-project-manifest/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Included — every behavior-change PR ships named regression tests
(repo constitution), and SC-002 mandates a seeded-fault test per drift class.

**Organization**: One phase per user story; each story lands as ONE
reviewable PR (plan.md slicing). The whole spec is the commitment.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [X] T001 Create branch `feat/016-doctor-project-manifest` in a worktree off origin/main (git-workflow rule); verify `.venv/bin/python -c "import devclaw; print(devclaw.__file__)"` prints the worktree path

*(No other setup — existing repo, no new dependencies.)*

## Phase 2: Foundational

**Purpose**: the report model + doctor package skeleton both US1 checks and US2/US3 checks plug into.

- [X] T002 Create `devclaw/doctor/model.py` — `Verdict` (str enum: ok/warn/fail/unknown), frozen `Finding` (check_id, verdict, evidence, remedy, project_id), `DoctorReport` (healthy, findings) with a `to_dict()` producing the contract shape from `contracts/doctor-tool.md` (counts, deterministic ordering, no timestamps)
- [X] T003 Create `devclaw/doctor/__init__.py` — `run_doctor(store, goal_store, registry, *, project_id=None) -> DoctorReport` facade: iterates the ordered instance-check tuple then per-project tuples (projects sorted by id), wraps every check in try/except so a crashed check yields an `unknown` finding with the error as evidence (FR-005), never mutates

**Checkpoint**: model importable, facade returns an empty-check report.

## Phase 3: User Story 1 — Instance doctor (P1) 🎯 MVP → **PR 1**

**Goal**: one `doctor` verb (MCP + CLI) codifying the post-redeploy checklist — instance invariants + the manifest-free project checks.

**Independent Test**: seed each drift condition in a stubbed instance → doctor names it with the right remedy; `FakeClaude.calls == 0`; DB byte-identical before/after.

### Tests (write first, watch them fail)

- [X] T004 [P] [US1] Create `tests/test_doctor.py` — seeded-fault tests per instance class: missing migration meta key(s); legacy `goal_status.lifecycle` row; NULL `goal_deliveries.ref_id`; lingering `goal_docs` table / `inbox_ingest_cursor` column; absent credentials file; past/near `expiresAt`; unresolvable skills bundle; absent vs corrupt vs valid raw `run_schedule` meta key; active usage pause. Fixtures: `tests/goal_fakes.py` (`register_tmp_project`, `seed_goal`), real tmp SQLite via StateStore
- [X] T005 [P] [US1] Add to `tests/test_doctor.py` — project-check tests: dangling `goal_ids` entry (today's zero-finding gap) names `link_goal`; workspace-matching goal without `project_id`; `workspace_is_dispatchable` reason surfaced; plus the cross-cutting guards `test_doctor_spends_zero_tokens_and_writes_nothing` (FakeClaude.calls == 0 + DB bytes unchanged), `test_doctor_reports_healthy_affirmatively`, `test_doctor_is_deterministic`, `test_crashed_check_reports_unknown_never_omitted`

### Implementation

- [X] T006 [US1] Create `devclaw/doctor/checks_instance.py` — checks `instance.migrations.meta_keys`, `instance.legacy.*` (4 SELECT probes per research R2), `instance.auth.*` (4 mechanical probes per R3 — parse `expiresAt` from `.credentials.json` under `config.host_claude_dir()`, `.claude.json` via `claude_trust.config_path_for`, `CLAUDE_CODE_OAUTH_TOKEN` presence only, pause meta), `instance.skills.bundle` (import-guarded `runner.runner._skill_paths_for_root` against `engine/host.SKILLS_DIR` for all 4 kinds, per R4), `instance.schedule.raw_key` + `instance.schedule.dispatch` (raw meta read + `operator_block`, per R5)
- [X] T007 [US1] Create `devclaw/doctor/checks_project.py` — `project.workspace.preflight` (reuse `workspace_is_dispatchable` verbatim), `project.links.dangling`, `project.links.unstamped_goals` (per R6); wire both check tuples into the `run_doctor` facade
- [X] T008 [US1] Create `devclaw/server/tools/doctor.py` — `@mcp.tool async def doctor(project_id=None)`: binds `_state` singletons, unknown project → ToolError via `_resolve_project_or_reject` semantics, returns `json.dumps(report.to_dict(), indent=2)`; register in `devclaw/server/tools/__init__.py` (import line + re-export block)
- [X] T009 [US1] Add `doctor` subcommand to `devclaw/cli.py` — flat shape like `scorecard`, normal registry+goals path, `--project` / `--json` flags, exit 0 healthy / 1 on fail-or-unknown per `contracts/doctor-tool.md`; extend `tests/test_cli.py` with a doctor invocation test
- [X] T010 [US1] Docs: add doctor to `docs/INDEX.md` + a short `docs/runbooks/` note ("after every redeploy: run doctor") referencing the contract; run full suite + ruff + mypy; open **PR 1**

**Checkpoint**: doctor usable on the VPS with real value, zero manifest code.

## Phase 4: User Story 2 — Per-project manifest (P2) → **PR 2**

**Goal**: `devclaw.json` doorway + all four consumption seams + seeding.

**Independent Test**: onboard-seeded manifest; `surface: library` kills glob heuristics; mid-run manifest tamper has no gate effect; malformed manifest rejects dispatch loudly.

### Tests (write first)

- [X] T011 [P] [US2] Create `tests/test_project_manifest.py` — doorway tests: valid parse (all fields + camelCase keys per `contracts/devclaw-manifest.schema.json`); malformed JSON / non-object / bad enum / missing schemaVersion ⇒ `ManifestError`; `schemaVersion > SCHEMA_VERSION` ⇒ distinct "instance too old" error; absent file ⇒ None; unknown keys tolerated; `load_manifest(ws, ref=sha)` reads via `git show` from a real tmp git repo
- [X] T012 [P] [US2] Create `tests/test_manifest_gates.py` — `test_manifest_edit_inside_run_does_not_change_gate_inputs` (FR-009 named regression: commit `surface: app`, capture pre_run_sha, commit `surface: library` post-"run", settle evaluates app-surface); `surface: library` ⇒ library exemption on frontend diff; strictness most-specific-wins matrix (explicit-goal beats manifest; manifest beats default; no-manifest ⇒ current behavior); verify_cmd tier order `action > goal > manifest`; malformed manifest ⇒ loud dispatch rejection; absent manifest ⇒ defaults + no rejection; zero-token guard on all new paths

### Implementation

- [X] T013 [US2] Create `devclaw/project_manifest.py` — constants `MANIFEST_NAME`/`SCHEMA_VERSION`/`BOILERPLATE_REVISION`, frozen `Manifest`, `ManifestError`, `parse_manifest`, `load_manifest(workspace_dir, ref=None)` per research R7 and data-model.md validation rules (fail-loud posture; `git show` mechanism from slice_guard, error posture from task_change)
- [X] T014 [US2] Strictness explicitness in `devclaw/goal/store/base.py` + `devclaw/goal/models.py` — persist `strictness` only when explicitly set; loader exposes `strictness_explicit: Optional[Strictness]` (raw, None when absent) while `Goal.strictness` stays resolved-non-null; add pure `effective_strictness(explicit, manifest_default)` (unrecognized ⇒ "strict", fail-closed) in `devclaw/project_manifest.py`
- [X] T015 [US2] Wire resolution at the goal-level read sites — `devclaw/goal/engine.py` (dispatch snapshot, 3 sites: strictness + `action.verify_cmd or goal.verify_cmd or manifest.verify_cmd`), `devclaw/goal/evaluator.py:665` (done-gate), `devclaw/goal/tick_settle.py:372` (slice guardrail); manifest read from worktree at these pre-run sites, never on idle paths
- [X] T016 [US2] Browser-gate surface at settle — `devclaw/quality/task_gates.py` `_browser_gate_failure` and `devclaw/queue/settle.py` browser seam accept a surface override resolved from `load_manifest(workspace_dir, ref=pre_run_sha)` (per R10); `library` short-circuits as the existing exemption, absent/`app` keeps default globs byte-identically
- [X] T017 [US2] Loud malformed-manifest rejection — worktree manifest validation in `devclaw/server/tools/_common.py::_preflight_or_prep` and the goal-dispatch preflight path; `ManifestError` → actionable rejection naming file + parse error (FR-010/R11)
- [X] T018 [US2] Seeding — `devclaw/speckit_setup.py::install_speckit_pr` writes a seed `devclaw.json` (schemaVersion 1, current boilerplateRevision, `$schema` URL) when absent, same reviewable PR; amend `runner/skills/onboard/00-onboard.md` to name `devclaw.json` human-owned (agent must not author/edit); update `tests/test_onboard_speckit.py`
- [X] T019 [US2] Docs: `docs/reference/devclaw-manifest.md` (schema, precedence table, examples), copy schema to `docs/reference/devclaw-manifest.schema.json`, INDEX.md rows; full suite + ruff + mypy; open **PR 2**

**Checkpoint**: declaration replaces inference; all structural guards green.

## Phase 5: User Story 3 — Drift detection + guided migration (P3) → **PR 3**

**Goal**: version-transition story closed: doctor detects, re-onboard migrates, human merges; the convention rider lands.

**Independent Test**: bump `BOILERPLATE_REVISION` → doctor names repo behind + onboard remedy; unpaired marker → integrity finding; mutated scaffold file → drift finding.

### Tests (write first)

- [ ] T020 [P] [US3] Extend `tests/test_doctor.py` — revision-behind finding (monkeypatched constant) naming both revisions + `onboard` remedy; `devclaw:managed` marker integrity (missing end, duplicated start) in a fixture AGENTS.md; `.specify/` scaffold drift vs `_resolve_speckit_source` (mutate one scaffold file); manifest presence/validity findings (`project.manifest.presence` warn, `project.manifest.valid` fail, schema-too-new)

### Implementation

- [ ] T021 [US3] Extend `devclaw/doctor/checks_project.py` — `project.manifest.presence`, `project.manifest.valid`, `project.manifest.revision`, `project.markers.integrity`, `project.scaffold.drift` per research R12 scope (marker literals defined once as module constants here or in project_manifest.py — no third home)
- [ ] T022 [US3] Re-onboard migration path — `devclaw/speckit_setup.py`: when `devclaw.json` exists but `boilerplateRevision < BOILERPLATE_REVISION`, the onboard/install PR path updates ONLY the mechanical fields (schemaVersion/boilerplateRevision), preserving every human-set field byte-for-byte; named regression test in `tests/test_onboard_speckit.py`
- [ ] T023 [US3] Convention rider — add to `CLAUDE.md` Conventions: "a PR that changes persisted state shape or in-repo boilerplate ships its doctor check, like it ships its named regression test" (FR-014); note the check-registry pattern in `devclaw/doctor/__init__.py` docstring
- [ ] T024 [US3] Docs sweep: INDEX.md currency tags for every doc this arc touched; full suite + ruff + mypy; open **PR 3**

**Checkpoint**: whole-spec scope met; nothing left "SPECIFIED, NOT IMPLEMENTED".

## Phase 6: Polish & Cross-Cutting

- [ ] T025 Run quickstart.md validation end-to-end (all three story blocks) in the worktree; confirm suite count ≥ pre-arc baseline
- [ ] T026 Verify structural guards explicitly: `test_config_single_doorway.py` (zero new env reads), `test_views_never_read_back.py`, `test_env_vars_doc_sync.py` (no new env vars), the ~20 zero-token guards in `tests/test_goal_tick.py`

## Dependencies & Execution Order

- Phase 1 → Phase 2 → US1 (PR 1) → US2 (PR 2) → US3 (PR 3) → Polish.
- US1 is independently shippable (MVP). US2 depends only on Phase 2 + the
  doorway (T013) — it could proceed parallel to US1 in a second worktree,
  but the PRs stack cleanly sequentially (stacked-PR rules in
  `.claude/rules/git-workflow.md` apply if parallelized).
- US3 depends on US1 (report) + US2 (manifest/revision constants).
- Within stories: tests (T004/T005, T011/T012, T020) before implementation;
  doorway (T013) before consumers (T014–T018).

## Parallel Opportunities

- T004 ∥ T005 (same new file — write as one pass if solo), T011 ∥ T012
  (different files), T020 standalone.
- Inside US2: T014 ∥ T013 partially; T015/T016/T017 after T013+T014.

## Implementation Strategy

MVP = US1 (PR 1) — deployable to the VPS with immediate post-redeploy value.
Then US2, then US3, each validated per its checkpoint before the next
begins. Per constitution v2.4.0: P1 landing is not a stopping point — the
whole spec is the commitment.
