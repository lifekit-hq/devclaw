# Contract: runner ↔ host lines (additions for spec 021)

Transport unchanged: line-delimited `result: <json>` / `event: <json>` on the
runner's stdout, parsed by `devclaw/engine/runner_io.consume_runner_output`.
All additions are OPTIONAL fields — absent means "feature not exercised";
old runners/new hosts and vice versa stay compatible.

## Result JSON — new optional fields (all statuses)

| Field | Type | Meaning |
|---|---|---|
| `context` | object | `{ "used": int, "size": int }` — last observed usage_update; omitted when the agent never reported (never fabricated) |
| `tripwire` | object | present iff the tripwire fired: `{ "threshold_pct": int, "used": int, "size": int, "active_slice": "US<n>" \| null, "landed": bool }` — `landed=false` means the land-now follow-up turn did not complete cleanly |
| `chunk` | object | present when the slice watcher was armed: `{ "feature": "<dir>", "advanced_slices": ["US<n>", ...], "stopped_by_watcher": bool }` |
| `usage_absent_note` | string | present iff tripwire configured >0 but no usage stream was observed (FR-007 inert-loud) |

## Event line — new type

`{"type": "ContextTripwire", "source": "runner", "payload": {"used": int,
"size": int, "threshold_pct": int, "active_slice": "US<n>" | null}}`
— emitted at most once per session, at firing time. Persisted by the existing
`_append_task_event` path; `server/worker_events.decode_event` gains a
friendly case (generic fallback already renders it).

## stopReason semantics (tightened)

- `"cancelled"` observed WITHOUT a completed tripwire/watcher land-now
  follow-up turn ⇒ the runner emits `status: "error"` (fail closed). Today it
  leaks through as `"ok"` — that hole closes in this spec.
- A land-now follow-up turn's own outcome (ok/blocked/error) is what the
  result reports; `tripwire.landed` records that the sequence ran.

## Host settle obligations on these fields

- `tripwire` present ⇒ `record_problem(category="limit",
  kind="context_tripwire", recovered=<landed>)` (one line, per the problems
  docstring) — the SC-005 ratchet metric.
- `_PROMPT_TOO_LONG_MARKER` failures additionally read `tripwire.active_slice`
  / `chunk.feature` when present and stamp the oversized-slice mark into the
  failure detail consumed by `_advance_brief` (FR-008).

## Environment (sandbox)

| Var | Forwarded by | Read by |
|---|---|---|
| `DEVCLAW_CONTEXT_TRIPWIRE_PCT` | engine sandbox-env allowlist (same loop as `DEVCLAW_SANDBOX_MEMORY`/`_CPUS`) | `runner/runner.py` via `os.environ.get` (runner reads its own env; config.py doorway excludes `runner/` by design) |
