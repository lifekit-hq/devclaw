# Data Model: Unattended-Week Operation (Phase 1)

## GoalStatus additions (goal store, `devclaw/goal/models.py`)

| field | type | default | meaning |
|---|---|---|---|
| `pending_merge_pr` | str | `""` | PR URL whose merge is owed after an `achieved` verdict; non-empty ⇒ the advance path retries the merge instead of planning. Cleared on successful merge and on `cancel_goal`. |
| `merge_heal_attempted` | bool | `False` | The one bounded conflict-resolution increment (FR-017) has been spent for the current close attempt. Reset on successful merge and on `steer_goal` (a re-direction restarts the budget). |

Validation: `pending_merge_pr` non-empty is only legal in phases
`blocked` (kind `mechanical:merge_failed`) and `idle` (resume just fired).
Doctor check: no goal in phase `done` with `pending_merge_pr` non-empty.

## New blocked kind

`mechanical:merge_failed` — set with `blocked_on` naming the PR URL and the
mechanical reason (conflict-after-heal / closed-unmerged / forge error).
Human-gated: auto-heal never lifts it (the heal budget was already spent
getting here); `resume_goal` retries the merge; `steer_goal` re-directions.

## state_store `meta` keys (ControlPlaneMixin conventions: absence == off, corrupt == off)

| key | value (JSON) | writer | reader |
|---|---|---|---|
| `quiet_mode` | `{"until_ms": int\|null, "armed_at": int}` | `set_quiet_mode` MCP verb | `QuietNotifier` (lazy expiry: read past `until_ms` ⇒ self-disarm via delete) |
| `deploy_pending` | `{"sha": str, "goal_id": str, "since_ms": int}` | US1 close path (devclaw-repo merges only) | tick quiescence check |
| `deploy_last` | `{"from_sha": str, "to_sha": str, "triggered_ms": int, "outcome": "triggered"\|"expired"}` | tick quiescence check | operator surfaces / doctor |

## New table: `suppressed_pings`

```sql
CREATE TABLE IF NOT EXISTS suppressed_pings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms INTEGER NOT NULL,
    text TEXT NOT NULL
);
```

Written by `QuietNotifier.send` while armed; read back (ordered by `ts_ms`,
LIMIT-bounded) by the catch-up surface (FR-014). Never consulted for
decisions — it is a record, not state. Doctor check: table exists;
row count sanity (warn > 10k).

## Merge outcome (in-memory, `merge_on_close.py`)

`MergeOutcome` enum: `MERGED`, `ALREADY_MERGED`, `CONFLICT`,
`CLOSED_UNMERGED`, `ERROR` — plus `merged_sha: str | None` and
`detail: str`. `MERGED`/`ALREADY_MERGED` are success (FR-004);
`CONFLICT` routes to the heal budget; the rest are hard failures.
Persisted only via its consequences (goal log line, close ping text,
blocked_on) — no new table.

## State transitions touched

- `EXECUTING_IDLE --ACHIEVE--> DONE`: now reached only after
  `MergeOutcome ∈ {MERGED, ALREADY_MERGED}` (or non-goal-branch strategies,
  unchanged).
- `EXECUTING_IDLE --BLOCK(mechanical:merge_failed)--> BLOCKED`: new
  producer, existing LEGAL row (BLOCK is already legal from idle).
- `BLOCKED --UNBLOCK--> EXECUTING_IDLE` (resume): unchanged row; the advance
  path's new pending-merge branch consumes it.
- No new LEGAL rows required.
