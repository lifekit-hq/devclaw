# Tasks: One definition of the change

**Input**: [spec.md](./spec.md), [plan.md](./plan.md), issue #630

**Tests**: required — every behavior change on this repo ships a named
regression test (constitution, Development Workflow).

**Delivery shape**: ONE PR. The FRs are interlocked — delivery cannot stop
discovering what changed until materialization produces the artifact, and the
fallback ladder cannot be retired until the tree is guaranteed clean. A split
would leave `main` in the exact two-computations state the spec exists to end.

## Phase 1: The artifact (US1, FR-001/002/004/007)

- [x] T001 `devclaw/task_change.py` — `ChangeSet` + `materialize_worktree_sync`.
      Mechanical: `add -A` then amend-or-commit, never a request to the agent.
- [x] T002 `tests/test_task_change.py` — the headline regression:
      `test_a_change_made_only_of_files_the_agent_never_recorded_is_judged_in_full`
      on a real git fixture. Plus: agent-committed-everything is byte-identical,
      mixed recorded/unrecorded, ignored files excluded, non-repo ⇒ `no_repo`,
      broken git ⇒ `error`, empty ⇒ `no_change`.
- [x] T003 `devclaw/task_git.py` — `_git_diff_sync(host_dir, base, head)`: one
      two-point range diff, `None` on failure, **fallback ladder deleted** (US3
      AS#1).

## Phase 2: Judging (US1, FR-003/006/007/010)

- [x] T004 `devclaw/quality/gate_pipeline.py` — `GateInput.change_fn` +
      memoised `change()`; `diff()` delegates. `diff_fn` stays for direct
      constructions (keeps PR #637's tests compiling).
- [x] T005 `devclaw/quality/gate_policy.py` — `"materialize"` joins `ALWAYS_HARD`.
- [x] T006 `devclaw/task_queue.py` — `_materialize_worktree` / `_capture_change`
      module-global seams; `_MaterializeGate` between verify and test_integrity.
- [x] T007 `tests/test_materialize_gate.py` — `test_an_undeterminable_span_fails_the_task_closed_instead_of_reading_as_an_empty_change`,
      `test_materialize_runs_after_verify_so_a_verify_failure_costs_no_git`,
      `test_the_gate_chain_computes_the_span_exactly_once`,
      `test_a_scope_or_review_gate_sees_the_materialized_span_not_the_working_tree`.

## Phase 3: The projection (US1/US2, FR-009)

- [x] T008 `devclaw/task_queue.py` — `_diff_stats` counts binary files
      separately and says so.
- [x] T009 `tests/test_delivery_diff_stats.py` — stats come from the judged span
      and name their bounded part.

## Phase 4: Publication (US2, FR-005)

- [x] T010 `devclaw/delivery/__init__.py` — `judged_head` / `agent_authored`;
      drift check; `git add -A`/commit block skipped on the materialized path.
- [x] T011 `devclaw/task_queue.py` `_execute` — pass the judged head through.
- [x] T012 `tests/test_delivery.py` —
      `test_delivery_publishes_the_judged_head_without_rediscovering_the_change`,
      `test_delivery_fails_loud_when_the_workspace_drifted_from_the_judged_span`.

## Phase 5: No change / retry (US1, FR-006/011/012/013/014)

- [x] T013 `devclaw/task_queue.py` — `no_change` on the result for code-writing
      kinds; delivery skipped; retry-isolation reset removed.
- [x] T014 `devclaw/task_git.py` — `_git_reset_clean_sync` deleted.
- [x] T015 `devclaw/goal/models.py` + `goal/engine.py` + `goal/tick_settle.py` —
      `PollResult.no_change`; `delivered` excludes it.
- [x] T016 `tests/test_task_retry.py` —
      `test_a_retry_keeps_the_workspace_and_rejudges_the_whole_span_against_the_pinned_base`.
- [x] T017 `tests/test_no_change_outcome.py` —
      `test_a_code_task_that_changed_nothing_settles_done_without_publishing`,
      `test_a_no_change_settle_is_reported_as_no_progress_not_a_delivery`,
      `test_a_read_only_task_that_changes_nothing_still_counts_as_delivered`.

## Phase 6: Retire the compensations (US3, FR-003)

- [x] T018 `devclaw/quality/change_advisories.py` — the three relocated
      post-run checks over the one artifact.
- [x] T019 `runner/hooks/post-run.sh` + `pre-run.sh` — diff-reading checks and
      the `.devclaw-pre-head` sidecar removed.
- [x] T020 `runner/skills/_writes-code/90-commit.md` — demoted to message
      guidance (US3 AS#3).
- [x] T021 `tests/test_change_advisories.py` — including
      `test_the_repo_guide_advisory_does_not_fire_on_a_run_that_created_the_guide`
      (US3 AS#2) and `test_the_commit_skill_no_longer_claims_to_make_verification_correct`.

## Phase 7: Docs + suite

- [x] T022 `docs/architecture.md`, `docs/flows/task-execution.md`,
      `docs/reference/env-vars.md` (if touched), `docs/INDEX.md` currency tags.
- [x] T023 Full suite ≥ baseline (2075 passed / 4 skipped) + `ruff check .`.

## Dependencies

Phase 1 → 2 → (3, 4, 5) → 6 → 7. Phases 3/4/5 are independent of each other.
