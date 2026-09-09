# Quickstart: validating "a stop needs evidence"

The stubbed suite proves the invariants; the live instance proves the flow.
Contracts and the data model are referenced, not repeated.

## Prerequisites

```bash
pip install -e ".[dev]"
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q      # baseline green first
ruff check . && mypy && lint-imports
```

In a worktree, confirm the import path first:
`.venv/bin/python -c "import devclaw; print(devclaw.__file__)"` must print
the worktree path.

## Stubbed suite — the tripwires this feature must keep green

Named after invariants, not functions; each extends an existing class test
or adds a seeded-fault pair. No test for wording or rendering.

| tripwire class | test | proves |
|---|---|---|
| structural guard | `test_every_problem_raise_site_carries_evidence` (extends `tests/test_mechanical_blocks_are_recheckable.py`) | every `new_problem(` call in the package passes `evidence=`; a call without it fails the build naming file:line (US1 sc.7) |
| structural guard | `test_every_human_gated_block_raises_a_problem` (same module) | every BLOCK transition to a non-healable kind sits in a module that raises a Problem; the `mechanical:*` kind guards are unchanged |
| fail-closed gate | `test_uncited_pass_still_fails_closed` (parametrised across `intake_readiness.validate`, `quality.validate_review`, `evaluator.validate`) | `ready`/`approve`/`achieved` with no evidence keep today's refusal (FR-004, US1 sc.8) |
| fail-closed gate | `test_stale_claim_without_a_tree_citation_grades_not_stale` (extends `tests/test_intake_readiness_fail_closed.py`; parametrised: no field / empty / a path absent from the tree / a path present) | only the present path grades stale; the others grade not stale with the missing citation in `rationale` (#896) |
| fail-closed gate | `test_review_refusal_blocks_only_through_the_change` (extends `tests/test_review_gate.py`; parametrised: all blockers out of span+tree ⇒ approve with `dropped`; one in span ⇒ `request_changes` with the others `dropped`; a location in the pre-run tree but not the span ⇒ still blocks) | US3 sc.1–2 |
| fail-closed gate / done is a proposal | `test_done_gate_met_clauses_close_in_both_modes` (replaces the strict-hold pair in `tests/test_goal_evaluator.py`; parametrised on strictness) | every clause satisfied + `structural: concerns` ⇒ `achieved`, concerns preserved on the result; one unsatisfied clause ⇒ `off_track` in both modes (US2 sc.1–2) |
| brake machinery | `test_green_probe_report_never_holds_and_strict_raises_a_problem_citing_it` (extends `tests/test_env_cap_admission.py`; parametrised on strictness) | trust: no `mechanical:env` transition, a `dropped_claim` row with seam `env_report`; strict: a `needs_answer` Problem whose `evidence.citation` is `probe:registry:npm-github`, default `proceed` (US1 sc.2) |
| brake machinery | `test_readiness_revoked_park_cites_the_grade_or_the_owner` (extends `tests/test_goal_tick.py`) | all refs unready ⇒ the Problem's evidence is `report:grade:…` when a grade row exists, `owner` when none; a single unready ref among ready ones raises nothing and logs the citation (US1 sc.6) |
| brake machinery | `test_dispatch_cap_problem_names_the_last_failure_mechanism` (extends the cap test in `tests/test_goal_tick.py`) | `what` contains the last failed increment's first error line and evidence is `report:<task_id>` (US1 sc.5, FR-010) |
| brake machinery | `test_unproven_stop_drops_under_trust_and_asks_under_strict` (`tests/test_goal_tick.py`, drives `_unproven_stop` directly) | trust ⇒ `None` + catalog row + log; strict ⇒ blocked with `evidence.proven=False`, `drop` default, standard timebox; the timebox applies `drop` as a defaulted Decision and unblocks (FR-003) |
| zero-token idle | existing `test_blocked_goal_with_open_problem_costs_zero_cognition` | still green with the new field — a blocked goal ticks with `FakeClaude.calls == 0` |
| materialize span | `test_declared_backticked_path_counts_for_any_gate_input_source` (extends the spec 032 US3 case in `tests/test_task_change.py`) | `frontend/package.json` declared + install-key edit ⇒ `in_scope=True`; undeclared ⇒ still `gate_input_paths` (#895) |
| admission brake | `test_goal_verify_cmd_is_refused_under_a_manifest_verify_cmd` (extends the admission test; parametrised: manifest declares ⇒ refuse; no manifest ⇒ allow a full command, refuse a bare name; `set_verify_cmd` same; clear allowed) | #897 |
| doctor seeded-fault | `test_goal_problems_evidence_column_missing_detected` / `_present_is_ok`, `test_goal_decisions_missed_stage_column_missing_detected` / `_present_is_ok` (`tests/test_doctor.py`) | drop the column ⇒ FAIL naming it and "restart applies the migration"; present ⇒ OK |
| cognition fixture (mechanism guard) | `tests/cognition/fixtures/intake_readiness/fs493_stale_uncited.json` | the fs#493 grade replayed: `stale: true`, no citation, tree containing the defect file ⇒ `validate` yields not stale; and the same reply with `stale_evidence` naming line 35 ⇒ stale. The live run (`DEVCLAW_RUN_COGNITION_EVALS=1`) prints the grader's actual citation for a human to read |

Retired in the same PR (symmetric ratchet):
`test_done_gate_structural_concerns_still_block_under_strict`, the strict
half of `test_done_gate_taste_corrections_cannot_hold_a_met_contract_open`.

## Live proof — after deploy, read from the instance

1. **The stale grade cannot strand a goal.** `regrade_intake` on an issue
   whose fix is NOT on main and whose body says so. Expect: readiness
   unchanged or `devclaw-ready`; if the model claims stale, the comment
   names what it cited and that the claim was dropped;
   `list_problems(category="gate")` shows a `dropped_claim` row with
   `intake_staleness`.
2. **A green probe beats a worker's report.** On a project whose registry
   probe reads green, dispatch a task whose worker reports
   `BLOCKED: env — NODE_AUTH_TOKEN …`. Expect under `trust`: no
   `mechanical:env` hold, the goal log says "superseded by probe
   registry:npm-github", a `dropped_claim` row; under `strict`: a Problem on
   `get_goal` with `evidence.citation = "probe:registry:npm-github"` and
   `default = "proceed"`.
3. **The cap says why.** Let a goal hit the dispatch cap on a failing
   verify. Expect: the Problem's `what` carries the verify command and its
   first error line; `evidence.citation` is `report:<task_id>`.
4. **Met clauses close.** A goal under `strict` whose done-gate review
   reports every pinned clause satisfied and `structural: concerns`.
   Expect: `achieved`, merge-on-close, the concerns in the goal log as
   follow-ups, no `donegate_churn`.
5. **A refusal must point inside the change.** Under `strict`, a review
   whose only blocker names a table or file absent from the span and the
   pre-run tree. Expect: the task settles `done`, the PR body carries the
   dropped finding as an advisory, `list_problems` shows `review_refusal`.
6. **The cut numbers are readable.** `get_scorecard_metrics` →
   `interventions` per achieved goal; `get_loop_health` → devclaw-caused
   idle share; `list_problems(category="gate")` → `dropped_claim` counts by
   seam; `list_problems` → `design_stops` by stage. The spec's SC-001…
   SC-007 are read from these four, never remembered.
