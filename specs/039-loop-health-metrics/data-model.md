# Data Model: Loop Health Metrics (Phase 1)

All tables live in the one `devclaw.db`. StateStore owns the three new tables
(`state_store/schema.py` creates them; `state_store/health.py` is the only
writer). GoalStore owns the `goal_convergence` columns (`goal/state.py` ALTERs;
`goal/state_status.py` writes).

## `loop_spans` — idle attribution record (US1)

| column | type | meaning |
|---|---|---|
| `id` | INTEGER PK | |
| `cause` | TEXT NOT NULL | one member of the vocabulary below, verbatim |
| `start_ms` | INTEGER NOT NULL | interval start (the previous tick's end) |
| `end_ms` | INTEGER NOT NULL | interval end (the observing tick) |
| `ticks` | INTEGER NOT NULL DEFAULT 1 | ticks coalesced into this span |
| `detail` | TEXT NOT NULL DEFAULT '' | goal id / reason, ≤120 chars, display only |

Index: `(end_ms)`.

**Vocabulary** (`devclaw/loop_health.py`, the ONE definition):
- `working`
- existing block kinds verbatim: `needs_answer`, `bug`, `lost_ref`,
  `dispatch_cap`, `donegate_churn`, `mechanical:*`
- nothing-to-do (FR-003): `empty_backlog`, `no_goal_armed`,
  `all_planned_done`, `window_closed`, `paused`
- plan additions: `operator_hold` (owner's turn), `unobserved` (a heartbeat
  gap longer than three ticks — excluded from every rate)

**Bucket** (derived, FR-005a): `bucket_for(cause) ∈ {working, devclaw,
owner, no_work, unobserved}` — see research D3.

**Invariant**: spans are contiguous and non-overlapping (each new span starts
at the previous span's `end_ms`); the window sum equals observed elapsed time.

## `usage_ledger` — permanent per-run usage (US3)

| column | type | meaning |
|---|---|---|
| `id` | INTEGER PK | |
| `source` | TEXT NOT NULL | `worker` \| `cognition` |
| `ref_id` | TEXT NOT NULL | task id (worker) / trace row id (cognition) |
| `attempt` | INTEGER NOT NULL DEFAULT 0 | retry index for worker rows |
| `goal_id` | TEXT | identity link |
| `workspace_dir` | TEXT | identity link (project join key) |
| `kind` | TEXT | task kind / cognition role |
| `reported` | INTEGER NOT NULL | 1 iff the run reported real usage |
| `input_tokens` | INTEGER | NULL when not reported |
| `output_tokens` | INTEGER | NULL when not reported |
| `cache_read_tokens` | INTEGER | NULL when not reported |
| `cache_creation_tokens` | INTEGER | NULL when not reported |
| `cost_usd` | REAL | NULL when the provider reported none (OAuth) |
| `usage_source` | TEXT | `acp` \| `transcript` \| `cli_envelope` \| '' |
| `at_ms` | INTEGER NOT NULL | settle / call time |

Constraints: `UNIQUE(source, ref_id, attempt)`; index `(at_ms)`, `(goal_id)`.

**Rules**: a NOT-reported row is a row (FR-011) — it is what makes
"records vs reported" countable; retention never touches this table (FR-010);
rollups sum only `reported=1` rows and always carry `records` + `reported`
counts (FR-012).

## `intake_grades` — the grader's prediction, machine-readable (US6, FR-021)

| column | type | meaning |
|---|---|---|
| `repo` | TEXT NOT NULL | `owner/name` |
| `issue_number` | INTEGER NOT NULL | |
| `readiness` | TEXT NOT NULL | label landed (`devclaw-ready` / `needs-refinement`) |
| `claimed_units` | INTEGER | filer's claim as parsed from the body (NULL if unstated) |
| `assessed_units` | INTEGER | grader's assessment (NULL if it could not judge) |
| `sizing` | TEXT | `agreed` \| `needs_human` |
| `stale` | INTEGER NOT NULL DEFAULT 0 | |
| `graded_at` | INTEGER NOT NULL | |

PK `(repo, issue_number)` — a re-grade replaces the row (the latest grade is
the prediction that stood when the goal was created).

## `goal_convergence` — extended in place (US6, FR-022)

New nullable columns (lazy ALTER, existing rows read NULL = unknown):

| column | type | meaning |
|---|---|---|
| `claimed_units` | INTEGER | Σ filer claims over the goal's issues; NULL if any is missing |
| `assessed_units` | INTEGER | Σ grader assessments; NULL if any is missing |
| `prediction_issues` | TEXT | JSON `{"summed": [n...], "missing": [n...]}` |
| `dispatches` | INTEGER | worker tasks the goal consumed (`kind != review_repository`) |
| `steered` | INTEGER NOT NULL DEFAULT 0 | 1 iff a human steering line exists for the goal |
| `cost_tokens` | INTEGER | Σ reported ledger tokens for the goal; NULL when none reported |

`outcome` already distinguishes `achieved` from `abandoned` (FR-027).

## Read projections (no storage)

- **Loop health** (`telemetry.compute_loop_health`): window → per-cause
  seconds, per-bucket seconds, `not_stuck_rate` = (working + owner + no_work)
  / observed, `null` when observed = 0; `unobserved_seconds` alongside.
- **Self-heal** (`compute_self_heal`): Σ recovered / (Σ recovered + Σ
  terminal) over problems last seen in window; `null` on empty; raw sums +
  `basis`.
- **Usage history** (`compute_usage_history`): per calendar month, per
  source: tokens, cost, `records`, `reported`.
- **Cost per outcome** (`compute_cost_per_outcome`): `merged_goals`
  {count, tokens_total, tokens_per, cost_usd_per}, `merged_standalone_prs`
  {…}, `shipped_nothing` {count, tokens_total}, `unknown` {count,
  tokens_total}, `records`, `reported`, `tasks_without_record`.
- **Calibration** (`compute_calibration`): `n`, `min_sample`, `determinable`,
  `needed`, per predictor `{mae, exact_rate, within_one_rate}`, `excluded:
  {steered, cancelled, unpredicted}`, `rows` (per goal).
