# Data Model: Worker Context-Budget Invariant (spec 021)

No new tables, no schema migration. Every spec entity maps onto an existing
store or artifact; this file records the mapping and the validation rules.
(Per spec 016 FR-014: no persisted-state shape change ⇒ no new doctor check;
re-verify this claim at tasks time.)

## Entities

### Chunk (= speckit story-slice)

| Aspect | Realization |
|---|---|
| Identity | `(feature_dir, US<n>)` — story tag within `specs/NNN-*/tasks.md` |
| Declared scope | the slice's task rows (`- [ ] T00x [USn] …`) + its per-slice context entry in the feature's `plan.md` |
| Lifecycle | planned (unchecked) → in-progress (session dispatched) → delivered (settled-done task) / oversized (marked in failure detail) → re-sliced |
| Writer | worker sessions only (single writer) |
| Readers | runner (session-stop enforcement), host `slice_guard` (build-ahead verdict), console plan tab (`_read_plan`) |

### Chunk plan artifact (= committed speckit trio)

`specs/NNN-*/spec.md` + `plan.md` + `tasks.md` on the goal branch.
- Typed contract = the checkbox grammar (see `contracts/chunk-grammar.md`).
- Validation: a continuation dispatch whose current feature dir has an
  unparseable/absent `tasks.md` while settle records show a mid-arc goal ⇒
  FR-004 loud block (`blocked_kind` mechanical/corrupt-doc family, existing
  legible-block machinery).
- Rides `task_change.materialize_worktree_sync` (staged+committed at run end)
  ⇒ appears in `pre_run_sha..post_run_sha`, gates, and the PR — by design.

### Chunk done-ness (derived, never stored)

Authoritative source: settled deliveries — `goal_deliveries` rows appended at
`tick_settle._resolve_polling_action` (idempotent on `ref_id`), rendered into
the next brief via `increment_records` → `prompt_budget.cap_prior_increments`
(6,000-char cap = the FR-003 bounded-input mechanism). tasks.md checkboxes are
the worker's workspace memory; on conflict the settle record wins (a flipped
slice whose task settled `failed` carries failure context into the next brief).

### Context-usage signal

ACP `session/update` notifications with `sessionUpdate == "usage_update"`,
fields `used` (tokens consumed) and `size` (window). Tracked in
`acp_client.py` as latest-ratio; tolerant parsing (unknown shape ignored;
absent stream ⇒ ratio unknown ⇒ tripwire inert + one loud result note).
Existing additive `usage` accumulation (input/output tokens for telemetry) is
unchanged and separate.

### Tripwire firing (observability record)

- Runner `event:` line `{type: "ContextTripwire", payload: {used, size,
  threshold_pct, active_slice}}` → persisted to `events` via the existing
  `_append_task_event` path (no host change needed for persistence).
- Result-JSON marker (see `contracts/runner-result.md`) → host settle calls
  `record_problem(category="limit", kind="context_tripwire", recovered=True)`
  → one problems-catalog row, countable per goal/task/cycle (SC-005 metric).

### Oversized-chunk mark

A structured marker in the settled failure/delivery detail naming the active
slice. Consumed by `_advance_brief`'s failure-context branch (800-char cap
holds) to instruct re-slicing; never a new table. Identical-retry refusal:
dispatching the SAME unchanged slice after an oversized mark is refused at
brief-assembly time (the failure context demands a tasks.md re-slice first).

## Configuration

| Knob | Home | Default | Semantics |
|---|---|---|---|
| `DEVCLAW_CONTEXT_TRIPWIRE_PCT` | `devclaw/config.py` accessor (host) + `os.environ.get` in `runner/runner.py` (sandbox reads its own env); forwarded via the engine's existing sandbox-env allowlist | `75` | fire the land-now sequence at used/size ≥ pct; `0` disables |

Documented in `docs/reference/env-vars.md` in the same PR (doc-sync test).

## State transitions touched (no new states)

- Task settle: one new classification branch (tripwire marker → problem row,
  `recovered=True`); the `_PROMPT_TOO_LONG_MARKER` branch additionally stamps
  the oversized-slice mark when the runner reported an active slice.
- Runner session: new internal sequence `running → cancel(turn) →
  land-now turn → verify/materialize/result`. A `cancelled` stopReason
  OUTSIDE this sequence is now a fail-closed error (hole fixed), never `ok`.
- Goal loop: unchanged (`executing` lifecycle, existing brakes; chaining
  continues to dispatch until the done-gate closes).
