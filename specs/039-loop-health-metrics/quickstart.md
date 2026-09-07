# Quickstart: validating Loop Health Metrics

Prerequisites: `pip install -e ".[dev]"`; for the live scenarios a deployed
instance (the routes are read-only and safe to hit at any time).

## Stubbed (pytest)

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q \
  tests/test_loop_health_absent_is_never_zero.py tests/test_goal_tick.py tests/test_doctor.py tests/test_runner_acp.py
```

Expected: green; the FR-020 file proves every layer reads `None` on an empty
sample; the tick test proves `tick_all` on an idle store writes one loop
sample with `FakeClaude.calls == 0`; the doctor test proves a DB missing the
new tables FAILs with the restart remedy.

## Live — US1 (why is the loop not running)

1. With no live goal, wait two ticks (or poke the heartbeat twice).
2. `curl -s "$DEVCLAW/loop-health.json?window_hours=1"` → `idle.causes`
   contains `empty_backlog` (or `all_planned_done` once any goal has ever
   closed), bucket `no_work`, `not_stuck_rate` = 1.0 with `observed_seconds
   > 0`.
3. Block a goal (`needs_answer` via an open Problem) → the next tick attributes
   to `needs_answer`, bucket `owner`, and `not_stuck_rate` is unchanged.
4. Park a goal on `mechanical:merge_failed` → attributed verbatim, bucket
   `devclaw`, the rate drops.
5. `set_operator_hold(on)` → `operator_hold`, bucket `owner`.
6. During a pause: `paused`, bucket `no_work`, and `/metrics`
   `devclaw_pause_active 1` agrees.

## Live — US2

`curl -s $DEVCLAW/loop-health.json | jq .self_heal` → `rate` with `recovered`
and `terminal` sums and the `basis` line; on a fresh DB `rate` is `null`.

## Live — US3 (durable usage + the worker source)

1. After the deploy (sandbox image rebuilt), dispatch any task; when it
   settles, `sqlite3 devclaw.db "select source,reported,usage_source,input_tokens,output_tokens from usage_ledger order by id desc limit 3"`
   shows a `worker` row with `reported=1`, `usage_source=transcript`.
2. `jq .usage.tasks_with_usage` on `get_scorecard_metrics` is ≥ 1 for the
   first time since spec 011.
3. Advance past `DEVCLAW_TASK_RESULT_RETENTION_DAYS`; the row survives the
   compaction; `/usage.json .history.months` still carries the month.
4. The backfill: on first boot after deploy, `meta.usage_ledger_backfilled`
   exists and `select count(*) from usage_ledger where source='cognition'`
   ≈ the surviving cognition trace count.

## Live — US4

`get_scorecard_metrics` → `usage.cost_per_outcome` with `merged_goals` and
`merged_standalone_prs` as separate figures, `shipped_nothing`, `unknown`,
`records`/`reported`; the keys `tokens_per_merged_pr` /
`cost_per_merged_pr_usd` are gone.

## Live — US5

Open the console overview: the "Loop health" strip shows the five numbers;
a metric with no data shows `—` and "no data", never `0`. The Usage page
renders the monthly trend past the 30-day retention.

## Live — US6

1. `regrade_intake` an issue → `select * from intake_grades` has its row with
   `assessed_units`.
2. Close (or cancel) a goal created from that issue → its `goal_convergence`
   row carries `claimed_units`, `assessed_units`, `dispatches`, `steered`.
3. `curl -s $DEVCLAW/calibration.json` → `determinable=false`, `needed=N`
   until ten predicted, unsteered, achieved goals exist; the loop's behaviour
   is unchanged either way (FR-026).
