# Contract: corrected scorecard output

The shape returned by `compute_scorecard` and therefore by the
`get_scorecard_metrics` MCP tool and rendered by `devclaw scorecard` (CLI).
Legacy fields `merge_rate`, `first_pass_hit_rate`, `evaluator.steer_rate`
are REMOVED (FR-011: a surviving name must keep its meaning; these can't).
Every rate whose denominator is 0 is `null`, never 0.0 or 1.0.

```jsonc
{
  "window_hours": 336,                  // ratchet window (from config), still overridable per call
  "since_ms": 0, "computed_at_ms": 0,

  "convergence": {                      // US1 — per-goal, non-bench
    "goals_closed": 0,                  // achieved in-window with known rounds
    "first_pass": 0,                    // of those, rounds == 1
    "first_pass_rate": null,            // first_pass / goals_closed
    "rounds_median": null,
    "rounds_max": null,
    "abandoned": 0,                     // cancelled goals (any rounds)
    "rounds_unknown": 0                 // pre-feature closes in-window (US1 sc.4)
  },

  "pr": {                               // US2 — distinct PRs, non-bench
    "opened": 0,
    "merged": 0,
    "rejected": 0,
    "open": 0,
    "unknown": 0,                       // undeterminable state, named (FR-004)
    "decided_merge_rate": null,         // merged / (merged + rejected)
    "state_as_of_ms": null,             // oldest as-of among in-window decided reads;
                                        // null = ledger never refreshed → STALE
    "refresh_truncated": false,         // cap hit on last refresh (loud bound)
    "bench": { "opened": 0, "merged": 0, "rejected": 0, "open": 0, "unknown": 0 }
  },

  "steering": {                         // US3
    "human_steers": 0,                  // goal_steering rows, source NOT LIKE 'auto-%'
    "machine_correction_rounds_median": null   // alias of convergence.rounds_median,
                                               // the machine half of the old steer_rate
  },

  "ratchet": {                          // US4 — informational only, never actuates
    "thresholds": { "first_pass_rate": 0.70, "decided_merge_rate": 0.80, "window_days": 14 },
    "checks": {
      "first_pass_rate":    { "value": null, "pass": false },
      "decided_merge_rate": { "value": null, "pass": false },
      "wedge_free_window":  { "clean_cycles": 0, "total_cycles": 0, "pass": false }
    },
    "pass": false                       // AND of checks; a null value never passes
  },

  "tasks": { /* unchanged raw counts: total_terminal, done, failed, cancelled */ },
  "workspace_breaks_tripped": 0,        // unchanged
  "evaluator": { /* verdict distribution + structural_grades kept as raw
                    telemetry; the three removed rate fields are gone */ },
  "usage": { /* unchanged, non-ratchet (FR-012) */ },
  "estimate_notes": [ /* usage-envelope note kept; the two notes obsoleted
                         by this feature removed */ ]
}
```

Contract rules pinned by tests:
- `pr.*` counts PRs (PK on URL), never task rows.
- `convergence.*` weights goals, never verdicts.
- `ratchet.pass` is false whenever any input value is null or any in-window
  cycle is non-clean; the failing check is identifiable from `checks`.
- `state_as_of_ms` present and honest on every output; consumers render
  staleness, never hide it.
- Bench-project activity appears ONLY under `pr.bench` (and is excluded from
  `convergence`), so adding bench work changes no ratchet-facing number.
