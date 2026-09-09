# Tasks: A stop needs evidence

**Input**: Design documents from `specs/044-a-stop-needs-evidence/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ (seams.md · mcp-and-http.md · prompts.md), quickstart.md

**Tests**: the spec's Independent Tests and quickstart.md name the tripwires; only those are written (testing rule: a PR ships a test only when it touches a tripwire class). Each test task extends the named existing module; no new test module except the cognition fixture directory.

**Organization**: three increments, each ONE reviewable PR, stacked (`feat/a-stop-needs-evidence` → `-us2` → `-us3`; merge parent first, retarget child with the REST call, rebase `--onto`, per `.claude/rules/git-workflow.md`). The whole spec is the commitment; US1 landing is not a stopping point.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on an incomplete task)
- **[Story]**: US1 / US2 / US3 from spec.md
- Paths are repository-relative; the worktree for the branch is where work happens

---

## Phase 1: Setup

**Purpose**: a worktree per increment; the import path proven before any test runs.

- [ ] T001 Create the US1 worktree: `git worktree add <scratchpad>/wt-044-us1 -b feat/a-stop-needs-evidence origin/main`; run `.venv/bin/python -c "import devclaw; print(devclaw.__file__)"` from it and confirm the worktree path prints; run the baseline `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q` and record the green count in the PR body later
- [ ] T002 Read `contracts/seams.md` end to end before touching a raise site — it is the contract every task in Phase 3 is held to (claim → citation shape → check → snapshot → unproven direction)

---

## Phase 2: Foundational (the field, the leaf, the store — blocks every story)

**Purpose**: `Problem.evidence` exists, is persisted, is rendered, and every existing raise site fills it; nothing about behaviour changes yet.

**⚠️ CRITICAL**: `new_problem(evidence=)` becomes a required keyword in T005, so T009–T015 (every existing call site) must land in the same commit series or the package does not import.

- [ ] T003 Create `devclaw/goal/evidence.py` (research D1, D2): `@dataclass(frozen=True) Evidence(citation: str, check: str, proven: bool, detail: str = "")` with constructors `Evidence.owner()`, `Evidence.unproven(citation, check, detail)`, and `to_dict()` / `from_dict()`; the citation grammar `parse(citation) -> (shape, ref) | None` for the six shapes `path` · `clause` · `probe` · `manifest` · `decision` · `report`; the named checks as pure functions taking the snapshot as an argument: `path_in_tree(citation, tree: frozenset[str])` (strip `:line` / `#symbol`), `clause_pinned(citation, clause_ids)`, `probe_registered(citation, probe_ids)`, `manifest_declared(citation, manifest_keys)`, `decision_recorded(citation, decision_ids)`, `report_present(citation, present_ids)`; each returns an `Evidence`. Module docstring states: pure, no cognition, no fetch; imports nothing above `goal/`
- [ ] T004 Add the one best-effort git helper to `devclaw/goal/evidence.py`: `tree_paths(workspace_dir, ref="HEAD") -> frozenset[str]` running `git ls-tree -r --name-only <ref>` with a bounded timeout, returning `frozenset()` on any failure (never raises; the empty tree means no path citation can pass — the safe direction); document that callers invoke it only inside a seam that already spent its call (constitution III)
- [ ] T005 In `devclaw/goal/models.py`: add `evidence: Optional[Evidence] = None` to `Problem` (import from `.evidence`), add `missed_stage: str = ""` to `Decision`; in `devclaw/goal/problems.py`: make `evidence` a REQUIRED keyword of `new_problem(...)` (`evidence: Evidence`), add the fixed options `DROP` (`"drop"`, "Drop the claim and proceed", consequence per data-model.md) and `PROCEED` (`"proceed"`, "Proceed — the probe is green"), add `unproven_options(caller_options) -> tuple` = `(DROP, *caller_options)[:5]`, extend `render_for_human` with one line `evidence: <citation> (<check>)` or `evidence: none — <detail>` and, on a design Problem (check `report_present`, raised_by `worker_block`/`done_gate`), the line `when you decide, record the stage that should have caught this: missed_stage=intake_grade|admission|plan|none`; extend `to_dict` with `"evidence": evidence.to_dict() | None`; update the module docstring's list of raise sites
- [ ] T006 In `devclaw/goal/state.py`: append `"ALTER TABLE goal_problems ADD COLUMN evidence_json TEXT NOT NULL DEFAULT ''"` and `"ALTER TABLE goal_decisions ADD COLUMN missed_stage TEXT NOT NULL DEFAULT ''"` to the migration list (the same idempotent add-column pattern as `problem_id`); in `devclaw/goal/state_problems.py`: write `evidence_json` in `insert_problem`, read it in `_row_to_problem` (`''` ⇒ `None`), write/read `missed_stage` in `insert_decision` / `_row_to_decision`
- [ ] T007 [P] In `devclaw/state_store/problems.py`: add `DROPPED_CLAIM_KIND = "dropped_claim"` beside `ENV_DEFICIENCY_KIND` with the docstring from data-model.md (category `gate`, message `"<seam>: <claim[:200]> — cited <citation or 'nothing'> (<check>)"`, `recovered=True`); add a helper `dropped_claim_message(seam, claim, evidence) -> str` so every seam spells the row one way
- [ ] T008 [P] In `devclaw/doctor/checks_instance.py`: add `check_goal_problems_evidence_column` (`instance.goal_problems.evidence_column`) and `check_goal_decisions_missed_stage_column` (`instance.goal_decisions.missed_stage_column`) following `check_goal_status_ci_green_head` (PRAGMA table_info; FAIL naming the column + "restart applies the migration"; OK when present); register them where the sibling checks are registered; add the seeded-fault pairs `test_goal_problems_evidence_column_missing_detected` / `_present_is_ok` and `test_goal_decisions_missed_stage_column_missing_detected` / `_present_is_ok` to `tests/test_doctor.py`
- [ ] T009 Fill evidence at the design-stop site (seams row 4) in `devclaw/goal/tick_settle.py` worker honest-block: `evidence=_evidence.report_present(f"report:{ref.id}", {ref.id})` (proven by construction); when the text after `BLOCKED:` contains a recommendation sentence, keep the options as today (`WORKER_BLOCK_OPTIONS`) — the worker's finding is `what`
- [ ] T010 Fill evidence at the dispatch-cap site (seams row 5, FR-010) in `devclaw/goal/tick_dispatch.py`: find the last increment record with an error; set `what = f"dispatch cap {cap} reached — last failure: {first_line(error)[:300]}"` (keep "review the open PRs" only when some increment delivered); `evidence=report_present(f"report:{rec.task_id}", ...)`; when no record has an error, `evidence=Evidence.unproven("", "none", "no failed increment carries an error line")` (still raised — the cap is a fact; the marker tells the owner the mechanism is unknown)
- [ ] T011 Fill evidence at the done-gate sites (seams rows 6, 7, 9) in `devclaw/goal/tick_donegate.py`: `needs_human`/`stalled` → `clause:<pinned id>` of the first unsatisfied clause via the active pin (`clause_pinned`), else `report:<review task id>` (`report_present`); churn park → `clause:<first unsatisfied pinned id>` (`clause_pinned`); `_block_on_ci_definition` → `manifest:ci` with `manifest_declared` over `{"ci"}` and `detail=rc.state`
- [ ] T012 Fill evidence at the closed-contract site (seams row 10) in `devclaw/goal/tick.py`: `clause:<first unsatisfied pinned id>` via the pin (`clause_pinned`); when the pin has no unsatisfied clause the existing first-pass propose-done path runs instead (unchanged) — assert that ordering in a comment
- [ ] T013 Fill evidence at the readiness-revoked park (seams row 2) in `devclaw/goal/tick.py`: read `store.intake_grade(repo, number)` for the first unready open ref (the StateStore accessor in `state_store/health.py`; expose it through `GoalStore` if not already reachable); when a row exists and `readiness != READY_LABEL` → `report:grade:<repo>#<n>` (`report_present`); when none → `Evidence.owner()` (a hand-pulled label); the single-unready-among-ready log line also names the grade or "label removed"
- [ ] T014 Fill evidence at the admission-lint site (seams row 11) in `devclaw/goal/service.py` `create_goal`: `decision:<id>` when an admission rewrite Decision was recorded (`decision_recorded`), else `report:grade:<repo>#<n>` for a pointer goal, else `Evidence.owner()`
- [ ] T015 Give the four human-gated mechanical kinds a Problem (seams row 15, FR-001 — kept in US1 per the plan): in `devclaw/goal/tick_guards.py` (`mechanical:lost_ref`, `mechanical:env_cap` give-up) and `devclaw/goal/tick_donegate.py` (`mechanical:merge_failed`) and `devclaw/goal/tick.py` (`mechanical:corrupt_doc`), raise a Problem in the SAME transaction as the BLOCK with `kind=<the mechanical kind>`, `raised_by=<site>`, fixed options (`SUPPLY`/`CANCEL` for env_cap and lost_ref; `CORRECT`/`CANCEL` for corrupt_doc and merge_failed), `timebox_s=0`, `evidence=report_present("report:<task_id>")` or `Evidence.owner()` where no task exists; set `problem_id` on the status; `HUMAN_GATED_MECHANICAL_KINDS` and every heal branch untouched; `resume_goal` stays the exit (verify `resume_goal` supersedes the open Problem — `supersede_open_problems` — as it does for other kinds)
- [ ] T016 Extend the structural guard in `tests/test_mechanical_blocks_are_recheckable.py` (research D12): `test_every_problem_raise_site_carries_evidence` — AST-scan every `new_problem(` call in `devclaw/` and fail naming file:line when the call lacks an `evidence=` keyword; `test_every_human_gated_block_raises_a_problem` — every `Event.BLOCK` transition whose status literal sets a `blocked_kind` that is neither in the healable set (`SELF_HEALING_BLOCK_KINDS`) nor `"bug"` sits in a module that calls `raise_problem` within the same function (walk the enclosing `FunctionDef`); the docstrings name the class (a stop that never reaches the Problem seam)
- [ ] T017 Read shapes: `devclaw/server/routes/goals.py` and the `get_goal`/`list_goals` builders in `devclaw/goal/service.py` emit `problem.evidence` (via `to_dict`) and `decisions[].missed_stage`; `console/src/pages/GoalDetail.tsx` renders one line under the Problem text: the citation with its check, or **no evidence** with the detail, and on an unproven stop the `drop` badge is the recommendation (the #899 `recommended` read already follows `problem.default`)
- [ ] T018 Run the suite; every existing Problem test in `tests/test_goal_tick.py` / `tests/test_goal_transactional.py` stays green with the field present; `ruff check .`, `mypy`, `lint-imports` clean (`goal/evidence.py` must appear as a leaf-safe module — no upward edge)

**Checkpoint**: every Problem in the instance carries evidence or says it has none; no behaviour changed; the guard fails a raise site that forgets.

---

## Phase 3: User Story 1 — Every stop is a Problem, and a Problem carries evidence (Priority: P1) 🎯 MVP

**Goal**: an unproven stop is dropped under `trust` and judged under `strict`; the four instances (#891 strict half, #895, #896, #897) become citations at their seams; a design stop's Decision records the stage that missed it; the constitution carries the rule.

**Independent Test** (spec): exercise each stop path in the stubbed suite with a claim and no citation; under `trust` the goal is not parked and a `dropped_claim` row exists; under `strict` the Problem is marked *no evidence* with the drop default. Add a stop path that bypasses the Problem seam; the guard fails naming it (T016).

### The rule

- [ ] T019 [US1] Add `_unproven_stop(goal_id, goal, status, *, seam, claim, evidence, options, store, notifier, consume_steering=None) -> GoalStatus | None` to `devclaw/goal/tick.py` (research D3): resolve strictness with `project_manifest.resolve_goal_strictness(goal)` (ManifestError ⇒ treat as `strict`, loud); under `trust` → `store.record_problem(category="gate", kind=DROPPED_CLAIM_KIND, message=dropped_claim_message(seam, claim, evidence), recovered=True, goal_id=...)`, `append_log(f"dropped claim ({seam}): {claim[:200]} — {evidence.detail}")`, return `None`; under `strict` → `new_problem(kind="needs_answer", raised_by=seam, what=claim, why="the claim carries no evidence devclaw can check", options=unproven_options(options), default_key="drop", evidence=evidence)` raised in one transaction with the BLOCK (`blocked_kind="needs_answer"`, `problem_id`), one OWNER notify, return the blocked status
- [ ] T020 [US1] Make `drop` executable: in `devclaw/goal/service.py` `resolve_problem` and `devclaw/goal/tick.py` `_apply_problem_default`, a Decision with `option_key == "drop"` records a `dropped_claim` catalog row for the Problem's `raised_by` seam and its `what`, then takes the ordinary UNBLOCK path (budget-restoring shape); the log line says `problem <id> → drop: claim dropped, proceeding`
- [ ] T021 [US1] Tripwire `test_unproven_stop_drops_under_trust_and_asks_under_strict` in `tests/test_goal_tick.py` (parametrised on strictness): trust ⇒ returns `None`, one catalog row with kind `dropped_claim`, no BLOCK transition, `FakeClaude.calls == 0`; strict ⇒ blocked, `problem.evidence.proven is False`, `default == "drop"`, `timebox_at > raised_at`; then advance past the timebox ⇒ a `defaulted` Decision with `option_key="drop"`, the goal idle, the catalog row written

### The intake staleness seam (#896, seams row 1)

- [ ] T022 [P] [US1] In `devclaw/prompts/intake-readiness.md`: add the `stale_evidence` sentence to "The staleness check" and the field to the output schema exactly as `contracts/prompts.md` states; add the FR-017 grounding line to the "Grounding" section; state each rule once
- [ ] T023 [US1] In `devclaw/intake_readiness.py`: add `stale_evidence: str = ""` to `ReadinessVerdict`; add `tree_files(workspace_dir) -> frozenset[str]` beside `repo_context` (same never-raises shape; `git ls-files` at the checkout; empty on failure); change `validate(parsed, *, tree: frozenset[str] = frozenset())` so `stale=True` requires `_parse_stale(...)` AND `evidence.path_in_tree(stale_evidence, tree).proven`; otherwise `stale=False`, `stale_evidence` kept verbatim, and `rationale` prefixed with `"stale claim dropped — cited nothing"` / `"stale claim dropped — <path> is not in the graded repository"`; `STALE_REASON` unchanged for the proven case
- [ ] T024 [US1] In `devclaw/intake.py` `grade`: collect `tree = await intake_readiness.tree_files(workspace_dir)` next to `repo_context` (outside any fail-closed try), pass it to `validate`; when the verdict carries a dropped stale claim, record `DROPPED_CLAIM_KIND` (seam `intake_staleness`) through the bound store when one is bound (best-effort), add the one-line drop notice to the posted comment, include the cited path in the stale comment, add `"stale_evidence"` and `"dropped_claim"` to the result dict and the `intake_grades` record (`stale` stays `False` on a drop); `regrade_intake` / `grade_backlog` docstrings in `devclaw/server/tools/intake.py` name the new keys
- [ ] T025 [P] [US1] Tripwire in `tests/test_intake_readiness_fail_closed.py`: `test_stale_claim_without_a_tree_citation_grades_not_stale` parametrised over (no field · empty · a path absent from `tree` · a present path) — only the present path yields `stale=True`; extend `test_prompt_asks_the_staleness_question_and_declares_the_output_field` to assert `stale_evidence` is in the schema block; keep the absent/garbled cases as they are
- [ ] T026 [P] [US1] Cognition fixture `tests/cognition/fixtures/intake_readiness/fs493_stale_uncited.json` (the fs#493 shape: an ask naming the hardcoded key at `backend/src/FinanceSentry.Infrastructure/Encryption/CredentialEncryptionService.cs:35`, a repo context + tree containing that file, two canned replies — `stale: true` with no citation ⇒ expected not stale; the same with `stale_evidence` naming line 35 ⇒ expected stale); extend `tests/cognition/harness.py` + a `test_intake_readiness_evals.py` mechanism guard mirroring `test_evaluator_evals.py` (mechanism-only by default; the live run behind `DEVCLAW_RUN_COGNITION_EVALS=1`); add the fixture to `tests/cognition/README.md`'s tree

### The worker env-report seam (#891 strict half, seams row 3)

- [ ] T027 [US1] In `devclaw/goal/tick_guards.py` `_block_on_env_deficiency`: after `record_worker_deficiency`, compute `target = env_cap.superseding_capability(cap_id)` and its persisted probe; when the probe reads **green**: under `trust` do NOT transition — log `"worker env report superseded by probe <target> (green): <item>"`, record a `dropped_claim` row (seam `env_report`), return `Outcome.CONTINUE` (or the caller's non-blocked outcome); under `strict` raise a Problem via `new_problem(kind="needs_answer", raised_by="worker_block", what=f"the worker reports the sandbox lacks {item}; the probe for {target} reads green", why="the probe is the fact; the report is a claim", options=(PROCEED, SUPPLY, CANCEL), default_key="proceed", evidence=probe_registered(f"probe:{target}", ...))` in one transaction with the BLOCK (`needs_answer`); red/unknown probe or no registered credential ⇒ today's `mechanical:env` hold with `evidence=report_present(f"report:{task_id}")` recorded on a Problem raised beside the hold (row 15 shape, `SUPPLY`/`CANCEL`, `timebox_s=0`); strictness via `resolve_goal_strictness(goal)`
- [ ] T028 [US1] Make `proceed` executable: in `resolve_problem` / `_apply_problem_default`, `option_key == "proceed"` clears the worker's deficiency row for that item (`env_cap.clear_worker_deficiencies` scoped to the cap id, or the narrow clear if one exists) before the UNBLOCK so the next admission read does not re-hold
- [ ] T029 [P] [US1] Tripwire in `tests/test_env_cap_admission.py`: `test_green_probe_report_never_holds_and_strict_raises_a_problem_citing_it` parametrised on strictness — trust: no `mechanical:env` transition, a `dropped_claim` row with seam `env_report`, `FakeClaude.calls == 0`; strict: a `needs_answer` Problem with `evidence.citation == "probe:registry:npm-github"`, `default == "proceed"`; red probe: the hold as today in both modes

### The verify_cmd refusal (#897, seams row 12)

- [ ] T030 [US1] In `devclaw/goal/admission.py`: replace `_check_bare_verify_cmd`'s warn with a `reject` condition (`code="bare_verify_cmd"`, message per `contracts/mcp-and-http.md`); add `_check_manifest_verify_cmd(verify_cmd, manifest_verify_cmd)` → `reject` (`code="verify_cmd_shadows_manifest"`, message naming the manifest command) when both are non-empty; `verify_goal` gains a `manifest_verify_cmd: Optional[str]` argument; in `devclaw/goal/service.py` `create_goal` pass the manifest's `verifyCmd` read at the merged base (`project_manifest.load_manifest_at_base`, best-effort ⇒ `None` when no manifest) and let the refusal raise `ValueError` → `ToolError` with nothing persisted; in `set_verify_cmd` apply both rules to a non-empty value, keep clear-to-empty allowed and say which command now runs; update the `create_goal` and `set_goal_verify_cmd` docstrings in `devclaw/server/tools/goals.py`
- [ ] T031 [P] [US1] Tripwire: extend the admission class test (the module that already covers `bare_verify_cmd` — `tests/test_goal_tick.py`'s admission cases or `tests/test_dispatch_memory_admission.py`, whichever holds `verify_goal`) with `test_goal_verify_cmd_is_refused_under_a_manifest_verify_cmd` parametrised: manifest declares ⇒ refused, nothing persisted; no manifest + full command ⇒ admitted; bare name ⇒ refused on both; `set_verify_cmd` non-empty under a manifest ⇒ refused; empty ⇒ allowed

### The declared-scope rule (#895, seams row 13)

- [ ] T032 [P] [US1] In `devclaw/task_change.py`: `in_scope_from_text` keeps every backticked, space-free token that is path-shaped (contains `/` or `.`) — not only `GATE_INPUT_GLOBS` matches; `build_paths` marks `in_scope=True` when `cls == GATE_INPUT` (from any source: the globs, `ENV_DECL_GLOBS`, the install-key regex) and a declared token matches the path by glob or equality; docstrings state "prose never counts; a declared product path widens nothing"; `devclaw/quality/README.md` names `package.json` among declarable paths
- [ ] T033 [P] [US1] Tripwire: extend the spec 032 US3 declared-scope case in `tests/test_task_change.py` with `test_declared_backticked_path_counts_for_any_gate_input_source` — `frontend/package.json` declared + an install-key edit ⇒ `in_scope=True` and not in `gate_input_paths`; undeclared ⇒ in `gate_input_paths`; a declared product path changes nothing

### FR-017 — the missed stage

- [ ] T034 [US1] In `devclaw/goal/service.py` `resolve_problem(..., missed_stage: str | None = None)`: validate ∈ {`intake_grade`, `admission`, `plan`, `none`} else `ValueError`; store on the Decision; response gains `"missed_stage"`; in `devclaw/server/tools/goals.py` add the optional `missed_stage` parameter to `decide` and `correct_implementation` with a docstring sentence naming the four values and when it matters; the HTTP resolve route in `devclaw/server/routes/goals.py` accepts the same field; `devclaw/goal/decisions.py` `_entry` appends `(missed: <stage>)` when set
- [ ] T035 [US1] In `devclaw/goal/service.py` add `design_stops(since_ms) -> dict` (resolved design Problems — `evidence.check == "report_present"` and `raised_by in {"worker_block", "done_gate"}` — grouped by the resolving Decision's `missed_stage`, `"unrecorded"` for `''`) through `GoalStore`; in `devclaw/server/tools/observability.py` `list_problems` and `devclaw/server/routes/observability.py` `/problems.json` add the `design_stops` key per `contracts/mcp-and-http.md`; docstring names it

### The constitution and the docs

- [ ] T036 [P] [US1] Amend `.specify/memory/constitution.md` Principle V (version 2.11.0, dated 2026-09-09, ruled by Denys 2026-09-09): one clause — every stop is a Problem and a Problem carries evidence; an unproven stop is dropped under `trust` and judged under `strict`; an uncited pass fails closed; the owner's Decisions are the sole exception; the structural axis and the review refusal clauses of US2/US3 are stated in their own PRs (T046, T053) — record the amendment history line
- [ ] T037 [P] [US1] Docs honesty: `CLAUDE.md` invariants bullet (the mechanical-blocks bullet gains the evidence rule and the row-15 Problems); `docs/architecture.md` Problems paragraph + the auto-heal paragraph; `docs/reference/intake-shape.md` (the staleness citation, the drop notice); `docs/flows/task-execution.md` env-block hop (green probe ⇒ superseded / strict Problem); `docs/INDEX.md` currency tags for each; the `~/.claude/skills/dispatch-ready/SKILL.md` grounding line is applied BY HAND on the PC (outside the repo — note it in the PR body as done or pending)
- [ ] T038 [US1] `/ship` ritual: full suite green, `ruff check .`, `mypy`, `lint-imports`; PR body states the baseline count, the named tripwires (T016, T021, T025, T029, T031, T033, the doctor pairs), the North-star case from the spec, and the two removed owner verbs (resume on false env holds, regrade after false stale); open PR `feat/a-stop-needs-evidence` → `main`

**Checkpoint**: US1 lands as ONE PR. A stale grade cannot strand a goal; a green probe beats a worker's report; a goal `verify_cmd` cannot shadow the manifest; a ticket's backticked `package.json` counts; the cap says why; every stop is visible as a Problem with evidence; `list_problems` counts dropped claims and design stops.

---

## Phase 4: User Story 2 — Pinned clauses met means the door is open (Priority: P2)

**Goal**: with every pinned clause satisfied, the structural axis never holds a close, in either mode; findings ride the close as follow-ups.

**Independent Test** (spec): seed a goal under `strict` whose review confirms every pinned clause and reports structural `concerns`; the goal closes on green CI with the concerns attached as follow-ups and no `donegate_churn` park.

- [ ] T039 [US2] Create the stacked worktree `wt-044-us2` on `feat/a-stop-needs-evidence-us2` from `feat/a-stop-needs-evidence`; verify the import path
- [ ] T040 [US2] In `devclaw/goal/evaluator.py` `validate` (research D8): once every clause is satisfied with non-empty evidence and no stub disguise applies, return `achieved` for both dial positions, keeping `structural_health` / `structural_concerns` on the result; delete the `structural_fail and strictness == "strict"` downgrade branch; fold the `off_track`-with-no-corrections-but-structural-concerns branch into the same rule when all clauses are satisfied (a model `off_track` with every clause met becomes `achieved` with the concerns preserved and the rationale noting the override); update the `(B) STRUCTURAL` sentence in `build_prompt` per `contracts/prompts.md`
- [ ] T041 [P] [US2] In `devclaw/prompts/goal-evaluator.md` line ~148: replace "the host applies the goal's strictness dial to the structural axis" with "the host records the structural axis as follow-ups on the close; it never holds a met contract open"
- [ ] T042 [US2] In `devclaw/goal/tick_donegate.py` `_close_and_merge`: when `followups` is non-empty, record one `dropped_claim`-class catalog row with seam `done_gate_structural` (kind `DROPPED_CLAIM_KIND`, message the first concern) so the cut condition for US2 is readable; the existing follow-up log line and notify count are unchanged
- [ ] T043 [US2] Symmetric ratchet in `tests/test_goal_evaluator.py`: remove `test_done_gate_structural_concerns_still_block_under_strict` and the strict half of `test_done_gate_taste_corrections_cannot_hold_a_met_contract_open`; add `test_done_gate_met_clauses_close_in_both_modes` parametrised on strictness (every clause satisfied + `structural: concerns` ⇒ `achieved` with concerns preserved; one unsatisfied clause ⇒ `off_track` in both modes; `poor` with every clause met ⇒ `achieved`); keep `test_done_gate_unmet_clause_still_fails_closed_under_trust`
- [ ] T044 [P] [US2] Docs: `docs/architecture.md` gate section (the structural axis is advisory in both modes; the dial no longer touches it), `CLAUDE.md` hardening bullet (the sentence "its structural axis rides the same dial … under strict they hold it open" is replaced), `devclaw/server/tools/goals.py` `set_goal_strictness` docstring (the done-gate is not dial-able on any axis), `docs/INDEX.md` tags
- [ ] T045 [US2] Verify the churn brake's assumption in `devclaw/goal/tick_donegate.py`: with T040 a churn park always has an unsatisfied pinned clause to cite (seams row 7); add an assertion-style comment and, in the T011 evidence fill, fall back to `Evidence.unproven("", "none", "no unsatisfied pinned clause — a malformed round")` only on the malformed-round path spec 035 already excludes from churn accounting
- [ ] T046 [US2] Amend `.specify/memory/constitution.md` Principle V (same 2.11.0 arc, second sentence): the done-gate's structural axis is advisory in both modes once every pinned clause is met; `/ship`; open the stacked PR against `feat/a-stop-needs-evidence`

**Checkpoint**: fs-431's shape (ten clauses met, three rounds on structure) closes on the first met round in `strict`.

---

## Phase 5: User Story 3 — A refusal points inside the change (Priority: P3)

**Goal**: a `strict` review refusal blocks only through findings citing a location inside the judged span or the pre-run tree; uncited findings are dropped and shown; a refusal with no surviving blocker approves with advisories.

**Independent Test** (spec): feed the review gate a diff and a verdict whose only blocker cites a path not in the span and not in the tree; the task settles `done` with the finding recorded as dropped and shown on the PR.

- [ ] T047 [US3] Create the stacked worktree `wt-044-us3` on `feat/a-stop-needs-evidence-us3` from `feat/a-stop-needs-evidence-us2`; verify the import path
- [ ] T048 [US3] In `devclaw/quality/__init__.py` (research D7): add pure `filter_blockers(review: dict, span_paths: frozenset[str], tree_paths: frozenset[str]) -> dict` — for each issue in `blocking`, take the `location` prefix (text before the first of `:`, `#`, ` `, `(`), treat it as a `path:` citation, keep it when `path_in_tree` passes against `span_paths | tree_paths`, else move it to `review["dropped"]` with `why="location not in the judged span or the pre-run tree"`; recompute `blocking` via `blocking_issues` and `verdict` by the existing reconcile rule; `format_feedback` appends a `Dropped (uncited) findings:` block when `dropped` is non-empty
- [ ] T049 [US3] In `devclaw/task_queue.py` `_review_failure`: accept `span_paths` and `base_sha` (threaded from `quality/task_gates._ReviewGate.check` via `GateInput.change()` → `ChangeSet.paths` / `base_sha`); after a `request_changes` verdict call `filter_blockers(review, span_paths, evidence.tree_paths(workspace_dir, base_sha))`; when `blocking` is empty after the filter, return `None` and hand the `dropped` list back through a new optional out-parameter or a `ReviewOutcome(feedback, dropped)` return consumed by the gate; when blockers survive, return `format_feedback(review)` (the dropped block included) — fail closed exactly as today
- [ ] T050 [US3] In `devclaw/quality/task_gates.py` `_ReviewGate.check` and `devclaw/queue/settle.py`: a review that passes with `dropped` findings attaches each as a gate advisory via `_attach_gate_advisory(result, "review", "<severity> at <location>: <text> — dropped: location not in the change")` so the PR body (`delivery.__init__` advisories block) and the task result carry them under `strict` as well as `trust`; record one `dropped_claim` row per refusal (seam `review_refusal`, `task_id` set) through the store
- [ ] T051 [P] [US3] Tripwire in `tests/test_review_gate.py`: `test_review_refusal_blocks_only_through_the_change` parametrised — (a) all blockers cite paths outside span+tree ⇒ `approve`, `dropped` non-empty, `blocking` empty; (b) one blocker in the span ⇒ `request_changes`, the others in `dropped`; (c) a blocker citing a pre-run-tree path not in the span ⇒ still blocks; (d) a prose-only location ⇒ dropped; plus the existing `test_validate_*` cases untouched; and in the settle test module that pins the review gate's fail-closed path, the crash case stays fail-closed (a crash is not a refusal and is never filtered)
- [ ] T052 [P] [US3] Docs: `docs/flows/task-execution.md` review hop (dropped findings ride the PR as advisories under both dials), `devclaw/quality/README.md` (the location rule), `docs/architecture.md` gate section, `CLAUDE.md` hardening bullet on the review gate under `strict`, `docs/INDEX.md` tags
- [ ] T053 [US3] Amend `.specify/memory/constitution.md` Principle V (same 2.11.0 arc, third sentence): a consulted review refusal blocks only through a location inside the judged span or the pre-run tree; `/ship`; open the stacked PR against `feat/a-stop-needs-evidence-us2`

**Checkpoint**: issue-493's shape (a refusal naming a dropped table) ships with the finding on the PR instead of two lost rounds.

---

## Phase 6: Polish & cross-cutting

- [ ] T054 Merge the stack in order per `.claude/rules/git-workflow.md` (parent without `--delete-branch`, retarget the child with the REST call, `git rebase --onto`, force-with-lease, CI green before each `gh pr merge`); remove the three worktrees
- [ ] T055 Deploy per `docs/runbooks/vps-waiter-deploy.md`; run `doctor` and confirm the two new column checks read OK; run quickstart.md live proofs 1–5 on the instance and paste the readouts into the vault STATUS page
- [ ] T056 Record the cut-condition baseline the day the stack deploys: `get_scorecard_metrics` interventions per achieved goal, `get_loop_health` devclaw-caused idle share, `list_problems(category="gate")` dropped-claim counts by seam, `list_problems` design_stops — in `~/memory/projects/devclaw/STATUS.md` with the date, so the 14-day cut reads are against a recorded start
- [ ] T057 Close #895, #896, #897 with a comment naming the merged PR and the seam row each became; note #891's strict half as landed

---

## Dependencies & execution order

- **Phase 1 → Phase 2 → Phase 3 (US1)** are strictly sequential: the field and the required keyword (T005) force every raise site (T009–T015) into the same series, and the guard (T016) is meaningful only once they are in.
- **US2 (Phase 4)** depends on US1 only for the evidence fill on the done-gate sites (T011) and the constitution version; it is stacked on US1's branch so the amendment history stays linear.
- **US3 (Phase 5)** depends on Phase 2's `evidence.tree_paths` (T004) and `path_in_tree` (T003); stacked on US2 for the same constitution reason. Its code touches `quality/`, `task_queue.py`, `queue/settle.py` — none of US2's files.
- **Within US1**, the five seam groups are independent of each other once T019 exists: intake (T022–T026), env (T027–T029), verify_cmd (T030–T031), scope (T032–T033), FR-017 (T034–T035) touch disjoint files.

### Parallel examples

- Phase 2: T007 and T008 alongside T003–T006; T009–T015 are seven files — run in parallel after T005.
- US1: T022 ‖ T025 ‖ T026 ‖ T029 ‖ T031 ‖ T032 ‖ T033 ‖ T036 ‖ T037 once T019–T020 are in; T023→T024 sequential; T027→T028 sequential; T030 then T031; T034→T035 sequential.
- US2: T041 ‖ T044 alongside T040.
- US3: T051 ‖ T052 alongside T048–T050.

## Implementation strategy

- **MVP = Phase 2 + Phase 3 (US1)** as one PR: the rule, the field, the guard, the four instances, FR-010, FR-017, the constitution clause. It is the story that catches the next seam.
- **US2 and US3** are each one small PR on the stack; neither is optional — each removes a judge-error compensation named in the north-star case, and the spec's cut conditions (SC-005, SC-006) are read for them separately.
- **Stated non-goals** carried from the plan: no seam registry, no new setting, no re-review round, no pin machinery for structural concerns, no evidence requirement on owner text.
