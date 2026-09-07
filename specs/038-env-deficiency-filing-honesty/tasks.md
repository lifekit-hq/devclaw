# Tasks: An environment gap is filed when the pipeline says it is filed

**Input**: Design documents from `/specs/038-env-deficiency-filing-honesty/`

**Prerequisites**: spec.md (clarified), plan.md

**Tests**: This feature touches the pause/brake family (the `mechanical:env` hold) and the loud-failure invariant, so the named cases below are REQUIRED. They EXTEND the existing worker-deficiency tripwires in `tests/test_env_cap_admission.py`; no sibling module, no instance test (rules/testing.md).

**Organization**: one phase per user story = one reviewable PR each (plan.md slicing).

## Phase 1: User Story 1 — the hold says what actually happened to the gap (P1) 🎯 MVP

**Goal**: a worker-reported environment gap is filed as devclaw work at the moment of the hold, and the log / hold text / owner ping carry the real outcome — an issue number and URL, or the rule or error that stopped it.

**Independent test**: spec.md US1 Acceptance Scenarios 1–5.

- [x] T001 [US1] `devclaw/goal/store/base.py`: thin `machine_issue_get` / `machine_issue_record` / `set_problem_issue` passthroughs to `_state`, in the shape of the existing `record_problem` / `get_meta` passthroughs (FR-009)
- [x] T002 [US1] `devclaw/goal/env_issue.py` (new): `EnvFilingOutcome` + `async file_env_deficiency(store, *, goal_id, project_id, item, cap_id, task_id, repo=None, gh=None, now_ms=None)` — env-gated on `DEVCLAW_SELF_REPO` (unset ⇒ stated no-op, no subprocess), builds the `MachineFinding` on the catalog fingerprint, files through `issue_doorway.file_finding`, links the catalog row on success, and returns the one-clause operator line; never raises (FR-001, FR-002, FR-003, FR-006, FR-007)
- [x] T003 [US1] `devclaw/goal/tick_guards.py`: `_block_on_env_deficiency` calls T002 before delegating, and `_block_on_env_cap` accepts an optional extra clause threaded into the log line, `blocked_on` and the owner ping (FR-004, FR-005)
- [x] T004 [US1] `devclaw/queue/settle.py`: the `_WORKER_ENV_MARKER` failure suffix names devclaw as the owner and stops asserting the filing; the marker's docstring says which layer states the outcome (FR-008)
- [x] T004a [US1] `devclaw/state_store/problems.py`: `ENV_DEFICIENCY_CATEGORY` / `ENV_DEFICIENCY_KIND` in the leaf both layers import, and `WORKER_ENV_SUFFIX_HEAD` in `devclaw/queue/settle.py` used by `devclaw/goal/tick_settle.py`'s item split — the two cross-layer literals this seam depends on stop being re-typed per layer (FR-002)
- [x] T004b [US1] `devclaw/goal/self_issue.py`: `should_close_stale` exempts `env_deficiency` — the age-out is the same deadlock as #818 one layer over (the hold makes the gap quiet by construction, so it would close its own unfixed issue) (FR-011)
- [x] T005 [US1] Tests — extend the existing class cases in `tests/test_env_cap_admission.py`: parametrize the worker-deficiency case over filed / already-tracked / doorway-failure / self-repo-unset, asserting the hold is identical in all four, the log names `#N` or the reason, exactly one `gh issue create` across a repeat, the catalog link, the `issue_filing_failed` row on failure, and `FakeClaude.calls == 0` throughout; plus the pure age-out exemption case (FR-011). The detail string is built from the queue's own marker/suffix constants, so a text edit that breaks the item split fails here
- [x] T006 [US1] Docs in the same PR: `docs/flows/task-execution.md` (the env-block hop now files and states the outcome), `docs/reference/env-vars.md` (`DEVCLAW_SELF_REPO`'s row says an unset value is stated in the hold, not silent), `docs/INDEX.md` currency tags

- [x] T006a [US1] **Post-landing correction**: US1 put a real `gh issue create` on a path the stubbed suite drives, gated only on an ambient `DEVCLAW_SELF_REPO`. CI runs on the host devclaw is deployed to, so the suite could behave differently there than in a sandbox — and could write to a live repository. `tests/conftest.py` now clears `DEVCLAW_SELF_REPO` (leaving the filing path the stated no-op FR-006 already specifies) and its spawn guard fails any `gh` WRITE verb beside the existing docker/tailscale/claude guards; reads stay allowed

**Checkpoint**: US1 alone closes #818 — every environment gap on a configured instance becomes exactly one issue within a heartbeat, and every gap on an unconfigured one says so out loud.

---

## Phase 2: User Story 2 — a mis-configured instance is visible before it swallows a gap (P2)

**Goal**: doctor reports an unset `DEVCLAW_SELF_REPO` on an instance whose catalog already holds worker-reported environment deficiencies.

**Independent test**: spec.md US2 Independent Test.

- [x] T007 [US2] `devclaw/doctor/checks_instance.py`: `check_self_repo_configured_when_env_gaps_exist` beside `check_goal_status_env_hold_notified`, registered in the instance check list. Keyed on the catalog holding `block/env_deficiency` rows, so it fires on the instance losing filings rather than on every dev checkout
- [x] T008 [US2] `tests/test_doctor.py`: the seeded-fault case (one registered workspace mutated across shapes — `_run`/`_findings` unpack ONE finding per check id). The `env` fixture also clears an ambient `DEVCLAW_SELF_REPO`, as it already does for the OAuth and registry tokens, or the seeded fault would read OK on a configured host
- [x] T009 [US2] Docs: the check's row in the doctor reference + `docs/INDEX.md` currency tag

---

## Post-landing corrections

- [x] T010 [US1] **FR-007 was only half-held.** `file_finding` records the failures it *sees*, but two of `file_env_deficiency`'s exits never reach it: the FR-010 wall-clock bound cancels it mid-call (`CancelledError` is not an `Exception`, so its handler never runs) and anything raised before it is entered escapes it entirely. Both returned a stated `NOT filed` clause with NO catalog row — #818's own silence, one level in, inside the change that closed #818. `devclaw/issue_doorway.py` grows `record_filing_failure` (FR-006's recording half as a verb a producer can call; `_fail` now delegates to it, so there is ONE shape and ONE kind for a failed filing however it failed), `devclaw/goal/env_issue.py` routes both exits through it via `_not_filed`, and the timeout's reason names the reconciliation risk it leaves behind (the create may have landed with no ledger row, which is how one gap could become two issues). The two exits are added to the existing parametrized failure case in `tests/test_env_cap_admission.py` — the class test, not a sibling; the hang case drives a real doorway call cancelled by a shortened bound rather than a stubbed `TimeoutError`, so it fails if the bound stops being enforced
- [x] T011 [US1] **The heal this spec's prose promised no longer exists.** Merging the default branch brought `specs/tiny/env-hold-observes-the-capability`, which deletes the `env_ref` green-rewrite: a worker-reported row stays red until a human vouches via `resume_goal`, because nothing can probe green a gap a worker named in prose. Every sentence US1 wrote about the hold healing "when the environment changes" was therefore false the moment the two branches met — including the body of the issue this spec files, which is where a human reads it. Corrected in `devclaw/goal/env_issue.py` (the filed issue's evidence names the `resume_goal` exit; its `proposed_done_when` drops the hold-clears clause, which is no longer repository behavior the resulting goal can reach), `devclaw/queue/settle.py`'s worker-env suffix, `devclaw/goal/tick_settle.py`'s routing comment, `docs/flows/task-execution.md`, and this spec's own Out-of-scope note. No behavior change — the same honesty ratchet as T010, applied to text a merge falsified

---

## Dependencies

- **US1** is self-contained. T001 precedes T002 (the passthroughs are its store surface); T002 precedes T003; T004 is independent of T001–T003 and can land in the same PR in any order.
- **US2** depends on nothing in US1 mechanically, but is only meaningful once US1 makes the unset self-repo an operator-visible fact.
