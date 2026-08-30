# Plan — spec 026 dispatch brief budget

## Files / areas this slice touches

- `devclaw/goal/prompt_budget.py` — add `STEERING_KEEP`, `STEERING_TRUNCATION_MARKER`, `cap_steering()`
- `devclaw/goal/tick.py` — import `prompt_budget`, apply `cap_steering()` in `_advance_brief`
- `devclaw/goal/tick_dispatch.py` — log `brief: N chars` after dispatch, pass `brief_chars` to trace
- `devclaw/loom/trace.py` — add `brief_chars: int = 0` to `DispatchEvent`, update `record_dispatch`
- `tests/test_advance_brief_budget.py` — three named regression tests (new file)

## Key decisions

- **Tail-keep for steering** (same pattern as `cap_prior_increments`): the newest
  line is at the tail, so `text[-STEERING_KEEP:]` always preserves the most
  recent correction. The `cap_section()` helper already does this.
- **Budget = 4 000 chars** for the steering section: enough for ~10-20 typical
  corrections (~200 chars each), an order of magnitude under the overflow point.
- **Goal log is the telemetry channel**: `store.append_log(goal_id, f"dispatch brief: {n} chars")`
  mirrors the size where operators already look.
- **Trace event** carries `brief_chars` for E2E harness visibility.
- **No total brief hard-cap** — the section-level caps bound each growing
  section; a blind total-cap would risk silently eliding the JSON output
  contract at the tail (scope note from prompt_budget.py).
