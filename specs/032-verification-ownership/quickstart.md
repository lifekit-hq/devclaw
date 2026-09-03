# Quickstart — validating spec 032 end to end

Prerequisites: the repo's dev venv (`pip install -e ".[dev]"`), a private tmpdir for
pytest (`TMPDIR=$(mktemp -d)`), and for the live checks a deployed instance reachable over
the devclaw MCP.

## Stubbed (the tripwire net) — runs in CI

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q \
  tests/test_goal_tick.py -k "remote_checks or mechanical_ci or blocked_kind" \
  tests/test_merge_on_close.py tests/test_env_cap_admission.py \
  tests/test_task_retry.py -k blocked tests/test_runner_blocked.py tests/test_runner_acp.py \
  tests/test_materialize_gate.py tests/test_gate_policy.py tests/test_runner_skills.py \
  tests/test_doctor.py -k "pending_done_proposal or ci_green_head or goal_interventions"
ruff check . && mypy
```

Expected: green; every zero-token guard (`FakeClaude.calls == 0`) intact. The full suite
runs before the PR (`pytest -q`, ~23 s with `-n auto`).

## Scenario walk-throughs (map to the spec's acceptance scenarios)

| story | scenario | how it is exercised | expected |
|---|---|---|---|
| US1 | red rollup on a done proposal | `FakeRemoteChecker(failing_names=("Backend CI",))`, tick a goal whose settled task header is `status=done` | no `review_repository` dispatch, `FakeClaude.calls == 0`, a steering row with source `auto-ci` naming "Backend CI", goal `idle` |
| US1 | pending rollup | `FakeRemoteChecker(state="pending")`, tick | `blocked_kind == "mechanical:ci"`, `pending_done_proposal == 1`, zero cognition; flip to passing, tick ⇒ review dispatched |
| US1 | head moved after green | achieved verdict scripted, `FakeRemoteChecker` returns a different `head_sha` at the merge read | no `_attempt_merge` call, re-hold |
| US2 | `BLOCKED: env` | fake agent `script_blocked_env`, settle | task failed with the env marker, not retried, one `block/env_deficiency` catalog row, project holds on `mechanical:env`, one ping |
| US2 | env_ref changes | rewrite the instance env_ref (image ref) and tick | the hold heals with no verb, dispatch proceeds |
| US3 | `AGENTS.md` + `.so` in the span | `_capture_change` stub with two `ChangedPath`s | task fails naming both paths in both dial positions, no retry, no delivery |
| US3 | issue declares `.github/workflows/*` | brief text containing the backticked glob | the workflow path classifies `product`, task delivers |
| US5 | seeded interventions | two `goal_convergence` achieved rows, one steer, one `commit` row | `interventions.per_achieved_goal == 1.0`, items listed |

## Live shakedown (after deploy) — `/live-shakedown` L2 + one manual probe

1. Register nothing new; pick a goal-branch goal on finance-sentry with a delivered PR.
2. Push a deliberately failing commit to its branch by hand (author ≠ devclaw), watch:
   the next done proposal does not open a review sandbox; `get_goal` shows the
   `auto-ci` steering; the scorecard's `interventions.items` lists the hand commit.
3. Revert; when CI is green the done gate opens and merge-on-close succeeds only after the
   merge-side re-read (visible as `done-gate remote checks … passing` in the goal log).
4. `doctor` reports OK for `instance.goal_status.pending_done_proposal_column`,
   `instance.goal_status.ci_green_head_column`, `instance.scorecard.goal_interventions`.
5. Confirm zero-token: `get_scorecard_metrics` evaluator call count does not rise while a
   `mechanical:ci` hold is in place across several ticks.

Links: [spec](./spec.md) · [research](./research.md) · [data model](./data-model.md) ·
[contracts](./contracts/).
