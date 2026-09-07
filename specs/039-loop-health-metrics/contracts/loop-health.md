# Contract: loop-health surfaces

Read-only, zero-LLM, auth per the transport middleware (same as every
`*.json` route). Every rate is `null` when its denominator is empty — never
`0` or `1.0` — and every figure carries the count it is based on.

## `GET /loop-health.json?window_hours=168` · MCP `get_loop_health(window_hours=168)`

```json
{
  "window_hours": 168, "since_ms": 0, "computed_at_ms": 0,
  "idle": {
    "not_stuck_rate": 0.93,
    "observed_seconds": 600000, "working_seconds": 300000,
    "unobserved_seconds": 1800,
    "buckets": {"devclaw": 42000, "owner": 120000, "no_work": 138000},
    "causes": [
      {"cause": "mechanical:ci", "bucket": "devclaw", "seconds": 42000, "ticks": 47, "last_detail": "issue-818-…"},
      {"cause": "needs_answer", "bucket": "owner", "seconds": 120000, "ticks": 133, "last_detail": "lkc-23-…"}
    ],
    "note": null
  },
  "self_heal": {"rate": 0.61, "recovered": 88, "terminal": 56, "problems": 41,
                "basis": "lifetime counters of problems last seen in window"},
  "clean_cycle": {"clean": 4, "total": 11, "rate": 0.36},
  "first_pass": {"first_pass": 6, "goals_closed": 24, "rate": 0.25},
  "cost": { "...": "the cost_per_outcome block below" }
}
```

- `idle.note` names a DB predating the table (`loop_spans absent`), else null.
- `clean_cycle` and `first_pass` are READ from their existing sources
  (`cycle_reports`, `goal_convergence`) with the scorecard's definitions,
  never recomputed differently (FR-016).

## `cost_per_outcome` (served inside `/loop-health.json` and inside the scorecard's `usage` block — the SAME dict, one definition)

```json
{
  "merged_goals": {"count": 7, "tokens_total": 1900000, "tokens_per": 271428, "cost_usd_per": null},
  "merged_standalone_prs": {"count": 3, "tokens_total": 210000, "tokens_per": 70000, "cost_usd_per": null},
  "shipped_nothing": {"count": 5, "tokens_total": 400000},
  "unknown": {"count": 2, "tokens_total": 90000},
  "records": 230, "reported": 190, "tasks_without_record": 12,
  "note": null
}
```

Replaces `usage.tokens_per_merged_pr` and `usage.cost_per_merged_pr_usd`
(FR-015a) — those keys no longer exist.

## `GET /usage.json` — gains `history`

```json
{ "...existing keys...",
  "history": {
    "months": [
      {"month": "2026-08", "worker": {"tokens": 0, "cost_usd": null, "records": 120, "reported": 0},
                            "cognition": {"tokens": 610000, "cost_usd": 31.2, "records": 80, "reported": 80}},
      {"month": "2026-09", "...": "..."}
    ],
    "backfill_boundary_ms": 1786000000000,
    "note": "rows before the boundary come from surviving transcripts; anything already pruned is absent, not zero"
  }
}
```

## `GET /calibration.json`

```json
{
  "min_sample": 10, "n": 4, "determinable": false, "needed": 6,
  "predictors": {"claimed": null, "assessed": null},
  "excluded": {"steered": 2, "cancelled": 3, "unpredicted": 15},
  "rows": [
    {"goal_id": "…", "outcome": "achieved", "rounds": 2, "dispatches": 3,
     "claimed_units": 2, "assessed_units": 3, "prediction_issues": {"summed": [812], "missing": []},
     "steered": false, "cost_tokens": null, "closed_at": "…"}
  ]
}
```

When `determinable` is true, each predictor is
`{"mae": 0.8, "exact_rate": 0.4, "within_one_rate": 0.9, "n": 12}`.

## Doctor

`instance.loop_health.tables` — FAIL when any of `loop_spans`,
`usage_ledger`, `intake_grades`, or the `goal_convergence.cost_tokens` column
is absent while goal tables exist (remedy: restart — bootstrap creates them);
WARN when tasks settled in the last 7 days but no worker ledger row is
`reported` (the worker usage source is silent — sandbox image / runner
predate 038 or the transcript path is not writable).
