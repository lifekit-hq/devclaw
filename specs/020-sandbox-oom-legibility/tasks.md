# Tasks: Sandbox OOM Legibility and Prevention

**Input**: Design documents from `specs/020-sandbox-oom-legibility/`
**Prerequisites**: plan.md, research.md (D1–D6), data-model.md, contracts/

Named regression tests are mandatory per the repo constitution (every
behavior-change PR ships one), so each story phase carries its test tasks.
One story = one increment = one reviewable PR (US1 → US2 → US3 → US4).

## Phase 1: Setup

(no project scaffolding needed — existing repo; the worktree/branch exists)

- [x] T001 Confirm baseline green in the worktree: full suite + ruff + mypy (`TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q`, `ruff check .`, `mypy`)

## Phase 2: Foundational

(blocking prerequisite for US1 and US3 — the marker/env names are shared vocabulary)

- [x] T002 Add `SANDBOX_OOM_MARKER = "sandbox OOM-killed"` and the evidence-string format helper to a seam importable by both runner and queue WITHOUT coupling them: define the literal in `devclaw/queue/settle.py` (`_SANDBOX_OOM_MARKER`) and independently in `runner/runner.py` (the runner is deliberately stdlib-only and self-contained; the contract doc `contracts/runner-oom-marker.md` is the single spec both cite in comments)

## Phase 3: User Story 1 — OOM death is legible and never retried unchanged (P1)

**Goal**: an OOM-killed agent session settles in ONE dispatch with a
cap-naming reason; the goal gets one adapted re-dispatch, then blocks with
`mechanical:env_cap`.

**Independent test**: stubbed runner returning a marker-carrying error →
task fails fast with the remedy text, runner called once; goal tick drives
adapted-brief-then-block; counter resets on productive settle.

- [x] T003 [US1] Runner cgroup evidence: in `runner/runner.py`, read `/sys/fs/cgroup/memory.events` (`oom_kill`) at session start, after agent-process death, and at exit; read `/sys/fs/cgroup/memory.max` (fallback `DEVCLAW_SANDBOX_MEMORY` env) — best-effort, unreadable ⇒ no evidence; on agent death with an `oom_kill` increase, prefix the terminal error with `sandbox OOM-killed (cap=<cap>, oom_kill=<n>): ` and skip any in-runner session re-attempt for this class
- [x] T004 [US1] Queue fast-fail: in `devclaw/queue/settle.py`, add `_SANDBOX_OOM_MARKER` branch beside `_PROMPT_TOO_LONG_MARKER` (same ordering discipline, before the retry-continue arm): `mark_failed` with the data-model.md reason template (cap + both remedies), breaker check, `return None`
- [x] T005 [P] [US1] Failure-class rule: add `("sandbox_oom", ("sandbox oom-killed",))` to `_FAILURE_CLASS_RULES` in `devclaw/state_store/rows.py`
- [x] T006 [US1] Goal counter: add `envcap_redispatches: int = 0` to `GoalStatus` across all five lockstep seams — `devclaw/goal/models.py`, `devclaw/goal/state.py` (DDL), `devclaw/goal/state_status.py` (upsert + read), `devclaw/goal/store/status.py` (frontmatter), `devclaw/goal/store/view_migration.py`
- [x] T007 [US1] Adapted brief: in `devclaw/goal/tick.py` `_advance_brief`, when `failure_context` carries the OOM marker, replace the generic "take a strictly smaller slice" advice with the cap-naming bounded-tooling directive (data-model.md); in the dispatch path increment `envcap_redispatches`
- [x] T008 [US1] Env-cap block: in `devclaw/goal/tick.py`/`tick_dispatch.py`, when the settled failure is the OOM class and `envcap_redispatches >= 1`, transition to `phase=blocked`, `blocked_kind="mechanical:env_cap"`, reason per data-model.md; reset the counter on productive settle in `devclaw/goal/tick_settle.py` beside `heal_attempts`
- [x] T009 [US1] Honest dispatch-cap message: in `devclaw/goal/tick_dispatch.py`, when the cap trips with zero delivered increments, the `blocked_on` reason carries the dominant terminal failure class instead of "review the open PRs"
- [x] T010 [P] [US1] Named regression tests in `tests/test_task_retry.py` (`test_sandbox_oom_fails_fast_without_retry_and_names_the_cap`, plus a quota-misroute shield case per the `test_quota_error_mentioning_prompt_too_long_still_pauses` pattern) and `tests/test_eval_outcomes.py` (class bucketing)
- [x] T011 [P] [US1] Named regression tests in `tests/test_goal_tick.py`: adapted brief content (presence of cap directive AND absence of "strictly smaller slice" for this class), block-after-one-adapted-retry, counter reset on productive settle, honest cap message with zero deliveries
- [x] T012 [US1] Runner-side test (fake cgroup dir fixture) proving marker emission with evidence and byte-identical behavior without, in the runner test module the suite already uses for runner behavior

## Phase 4: User Story 2 — memory exhaustion kills the workload, not the supervisor (P2)

**Goal**: runner-spawned and agent-spawned workloads carry raised
`oom_score_adj`; runner/agent stay default; happy path byte-identical.

**Independent test**: spawn-seam unit tests assert the preexec writes the
score and outputs are unchanged; the real-kill proof is quickstart step 1
(live shakedown).

- [ ] T013 [US2] Preexec shield: in `runner/runner.py`, add a module-level `_raise_oom_score()` preexec helper (write `800` to `/proc/self/oom_score_adj`, swallow errors) and wire it into `_run_verify`, `_run_one_hook`, and `_mise_run` spawns
- [ ] T014 [US2] Shield script: add `/opt/devclaw/oom-shield.sh` via `.sandcastle/Dockerfile` (`echo 800 > /proc/self/oom_score_adj 2>/dev/null || true`, chmod like the existing hooks layer)
- [ ] T015 [US2] Agent-side shield: in `runner/runner.py`'s agent env allowlist, set `BASH_ENV=/opt/devclaw/oom-shield.sh` (only when the file exists — host-engine runs without the image must not break)
- [ ] T016 [P] [US2] Named regression tests: runner spawn seams use the preexec and agent env carries `BASH_ENV` (and does NOT leak other env — extend the existing allowlist test); happy-path verify output unchanged

## Phase 5: User Story 3 — the worker can see its cage (P3)

**Goal**: the enforced sizing is declared into the sandbox and the worker
guidance bounds tooling by it.

**Independent test**: pure `_build_docker_args` assertions (declared env ==
enforced flags, same source); skill-content presence/absence test.

- [ ] T017 [US3] Engine declaration: in `devclaw/engine/sandcastle.py` `_build_docker_args`, add the third env-forward family `-e DEVCLAW_SANDBOX_MEMORY=<v> -e DEVCLAW_SANDBOX_CPUS=<v>` sourced from the same variables used for `--memory`/`--cpus`; update the `_build_payload`/docstring "two families" comment honestly
- [ ] T018 [US3] Agent visibility: add `DEVCLAW_SANDBOX_MEMORY`/`DEVCLAW_SANDBOX_CPUS` to the agent env allowlist in `runner/runner.py`
- [ ] T019 [US3] Worker guidance: extend `runner/skills/_writes-code/40-verify-iterate.md` — bound test-runner workers/heap by the declared allocation; name the `/proc/meminfo`/`nproc` host-lying trap; encode bounded-memory-first, wall-clock second
- [ ] T020 [P] [US3] Named regression tests: `tests/test_sandbox_isolation.py` same-source assertion (env pair equals the `--memory`/`--cpus` values, including when a per-project override later changes them); skill-content test asserting the guidance is present in the canonical file and matches the prompt-test presence/absence rule

## Phase 6: User Story 4 — per-project sandbox sizing (P4)

**Goal**: registry overrides flow registry → queue → EngineRequest → argv →
declared env, accounted by admission, rejected loudly when unadmittable.

**Independent test**: the `test_sandbox_image_override.py` chain pattern for
sizing; admission budget math with a per-task override; write-edge rejection
cases; doctor seeded fault.

- [ ] T021 [US4] Registry fields: append `sandbox_memory`, `sandbox_cpus` to `_OVERRIDE_STR_FIELDS` in `devclaw/project_registry.py`; add `Project` dataclass fields + `to_dict` keys (`sandboxMemory`/`sandboxCpus`)
- [ ] T022 [US4] Write-time validation: `_validate_sandbox_memory` / `_validate_sandbox_cpus` in `devclaw/project_registry.py` (grammar via `host_resources._parse_mem`; admittability against host MemTotal + `COGNITION_MEM_RESERVE`; message names both numbers), called from `create` and `update`
- [ ] T023 [US4] Write edges: `sandbox_memory`/`sandbox_cpus` params on `update_project` in `devclaw/server/tools/projects.py` (`"inherit"` → clear; `ToolError` translation) and in `devclaw/server/routes/projects.py` `_OVR_FREE_STR` + `_project_overrides`
- [ ] T024 [US4] Launch resolution: `TaskQueue._sandbox_sizing(project_id)` in `devclaw/task_queue.py` (pattern `_sandbox_image`); thread through `devclaw/queue/settle.py` dispatch sites and `devclaw/queue/programs.py`; new `EngineRequest.sandbox_memory`/`sandbox_cpus` in `devclaw/engine/__init__.py`; `_build_docker_args` gains `sandbox_memory`/`sandbox_cpus` kwargs defaulting to the module globals (enforced flags AND declared env from the same resolved values)
- [ ] T025 [US4] Admission accounting: parameterize `_mem_can_launch(effective_bytes)` / `_mem_commit_launch(effective_bytes)` in `devclaw/queue/admission.py`; resolve the effective value in the pump loop in `devclaw/task_queue.py` (which holds the pending row's `project_id`); leave the program-path gap recorded (research D6) — do not silently claim coverage
- [ ] T026 [US4] Doctor check: `project_sandbox_sizing` in `devclaw/doctor/checks_instance.py` — stored overrides parse and remain admittable on this host — with a seeded-fault test (spec 016 FR-014)
- [ ] T027 [P] [US4] Named regression tests: `tests/test_sandbox_sizing_override.py` (copy the `test_sandbox_image_override.py` chain wholesale), rejection cases in `tests/test_project_registry.py`, per-task admission math in `tests/test_dispatch_memory_admission.py`

## Phase 7: Polish & cross-cutting

- [ ] T028 Docs honesty pass: update `docs/reference/env-vars.md` (declared-into-sandbox semantics of the sizing vars; per-project override resolution), `docs/architecture.md`/`docs/flows/task-execution.md` where the settle classes or sandbox env are described, and `docs/INDEX.md` currency tags — in the same PRs as the changes that stale them
- [ ] T029 Verify each increment against `quickstart.md`'s stubbed matrix before its PR; run the live-shakedown steps once after US2 and once after US4 land and deploy
- [ ] T030 Close the loop: comment on devclaw#702 with the landed PR list; mark spec 020 status; re-enable the run window if still disabled (operator note from 2026-08-26)

## Dependencies

- Phase 2 (T002) precedes US1 and US3 (shared marker/env vocabulary).
- US1 (T003–T012) is independent of US2–US4 — MVP.
- US2 (T013–T016) independent of US1; touches the image (needs a sandbox-image rebuild on deploy).
- US3 (T017–T020) independent of US1/US2; T017 precedes T018/T019 within the story.
- US4 (T021–T027) depends on US3's T017 only in that it reuses the declaration family; the registry/queue/admission work is self-contained. T021 → T022 → T023/T024 → T025 → T026.
- Polish (T028–T030) rides each increment's PR (T028) or follows the last (T029/T030).

## Implementation Strategy

MVP = US1 alone (legibility + adapted retry) — it delivers the incident's
whole diagnostic value even if nothing else lands. Then US2 (prevention),
US3 (visibility), US4 (sizing), each as one PR, suite green + ruff + mypy
before each `gh pr create`, squash merges. The whole spec is the commitment;
any dropped story is said out loud in the spec with rationale.
