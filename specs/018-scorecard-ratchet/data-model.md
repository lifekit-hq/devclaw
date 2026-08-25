# Data Model: Scorecard Measures the Ratchet

## New table: `goal_convergence` (state_store, `devclaw.db`)

One row per terminal goal event — the settle ledger for convergence
(precedent: `eval_outcomes`).

| Column | Type | Notes |
|---|---|---|
| `goal_id` | TEXT PRIMARY KEY | one terminal row per goal id (cancel+refile = new id) |
| `outcome` | TEXT NOT NULL | `achieved` \| `abandoned` |
| `rounds` | INTEGER NOT NULL | done-gate rounds consumed; `0` = closed/cancelled before any proposal |
| `first_proposal` | INTEGER NOT NULL | 1 iff `outcome='achieved' AND rounds=1` (denormalized for the query) |
| `workspace_dir` | TEXT | project attribution via the existing ws-normalization |
| `closed_at_ms` | INTEGER NOT NULL | terminal transition time |

**Writer**: goal close path (`tick_donegate` achieved branch) and cancel
paths in `GoalService` — through one new store method
(`record_goal_close(...)`), written BEFORE any status-field reset.
**Readers**: `compute_scorecard`, doctor.

**Semantics**:
- `outcome='abandoned', rounds=0` → excluded from convergence denominator,
  counted in `abandoned`.
- `outcome='abandoned', rounds>0` → cancelled mid-churn: counted in
  `abandoned`, rounds excluded from the closed-goal distribution (spec edge
  case).
- Goal closed in-window with NO row → `rounds_unknown` bucket (pre-feature
  close). Detection: `goal_status.phase IN ('done','cancelled')` with a
  terminal `goal_phase_history` entry in-window and no convergence row.

## New table: `pr_ledger` (state_store, `devclaw.db`)

One row per distinct PR devclaw opened.

| Column | Type | Notes |
|---|---|---|
| `pr_url` | TEXT PRIMARY KEY | identity — distinct-PR counting is a PK fact |
| `workspace_dir` | TEXT | project attribution |
| `opened_at_ms` | INTEGER NOT NULL | first time the URL was observed on a settle |
| `state` | TEXT NOT NULL | `open` \| `merged` \| `rejected` \| `unknown` |
| `state_as_of_ms` | INTEGER | last successful platform read; NULL = never refreshed |

**Writers** (single writer per concern):
1. Row creation (`state='open'`, `state_as_of_ms=NULL`): the settle path
   that already records `eval_outcomes` upserts the URL on first sight
   (INSERT OR IGNORE — increments sharing a goal-branch PR touch one row).
2. State refresh: the cycle-report step only — batch update via
   `upsert_pr_states`, looking up only rows with `state IN ('open','unknown')`
   and `opened_at_ms` within the ratchet window, capped (default 50,
   truncation reported loudly).

**State transitions**: `open → merged | rejected | unknown`;
`unknown → merged | rejected | open` (a later successful read corrects it);
`merged` is terminal; `rejected` stays in the refresh set (a rejected PR
can be reopened — it re-enters `open` on the next refresh; merged cannot
un-merge).

## Extended: `Project` (project_registry)

| Field | Type | Notes |
|---|---|---|
| `bench` | bool = False | evidence/shakedown project: excluded from every ratchet-facing rate, reported separately; settable via `update_project` |

## Extended: config (config.py doorway)

| Env var | Default | Meaning |
|---|---|---|
| `DEVCLAW_RATCHET_FIRST_PASS` | `0.70` | per-goal first-pass threshold |
| `DEVCLAW_RATCHET_DECIDED_MERGE` | `0.80` | decided-PR merge-rate threshold |
| `DEVCLAW_RATCHET_WINDOW_DAYS` | `14` | rolling window; wedge-free condition over `cycle_reports.clean` in the same window |

## Read-side derivations (no storage)

- `first_pass_rate` = achieved rows with `first_proposal=1` / achieved rows
  (non-bench, in-window; None when denominator 0).
- `decided_merge_rate` = merged / (merged + rejected) over non-bench
  in-window `pr_ledger` rows (None when denominator 0).
- `human_steers` = `goal_steering` rows in-window with source NOT LIKE
  `'auto-%'`.
- `ratchet.pass` per metric + overall = comparisons against config; overall
  additionally requires every `cycle_reports` row in-window `clean=1`.
