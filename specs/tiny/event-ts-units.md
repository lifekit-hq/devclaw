# TinySpec: Event ts units — seconds written into the ms column

**Branch**: fix/event-ts-units
**Date**: 2026-08-30
**Status**: done
**Complexity**: small

## What

The runner emits event `ts` as `time.time()` (seconds); the host stores it
verbatim into the ms-scale `events.ts` column. 4,081 live rows (Aug 28–30)
carry seconds-scale ts: they render as 1970, break ts-ordered reads, and the
30-day ms-cutoff events prune classifies them as ancient and will DELETE them
on its next cycle — recent sandbox transcripts silently vanish. Fix at the
single writer (host normalization — deployed sandbox images lag, so
runner-side fixes alone can't be trusted), fix the four runner emissions, and
repair the existing rows idempotently.

## Context

| File | Role |
|------|------|
| `devclaw/state_store/observability.py` | Will be modified — `append_event` normalizes a seconds-scale ts to ms |
| `devclaw/state_store/schema.py` | Will be modified — idempotent repair UPDATE for existing seconds rows (rides `idx_events_ts`) |
| `runner/runner.py` | Will be modified — 3 emission sites → `int(time.time() * 1000)` |
| `runner/acp_client.py` | Will be modified — 1 emission site → same |
| `tests/test_state_store_guards.py` | Will be modified — extend the events guard: seconds ts normalizes; a just-written event is never prune-eligible |
| `devclaw/queue/settle.py` | Context — `_append_task_event` passes `int(event.ts)` through; normalization lands below it |

## Requirements

1. `append_event` stores ms regardless of input scale: `0 < ts < 10**12` ⇒
   seconds ⇒ ×1000 (10**12 ms = 2001; every real seconds value is far below,
   every real ms value far above). ts=None keeps the `_now_ms()` default.
2. All four runner emission sites emit `int(time.time() * 1000)`.
3. Schema-ensure runs an idempotent repair: `UPDATE events SET ts = ts*1000
   WHERE ts > 0 AND ts < 10**12` — naturally idempotent (repaired rows leave
   the range), cheap via `idx_events_ts`, and self-healing for stragglers from
   old sandbox images.
4. Tripwire test (retention/prune family) extends the existing events guard in
   `tests/test_state_store_guards.py`: a seconds-scale append reads back
   ms-scale, and a just-written event survives `maybe_prune_events`.

## Plan

1. Normalize in `append_event` (one line + comment naming the invariant).
2. Repair UPDATE in `schema.py` beside the lazy ALTERs.
3. Fix the four emissions.
4. Extend the guard test; full suite + ruff + mypy.

## Tasks

- [x] Host normalization in `append_event`
- [x] Idempotent repair in `schema.py`
- [x] Four runner emission fixes
- [x] Extend events guard test
- [x] Suite + ruff + mypy green

## Done When

- [x] A seconds-scale `ts` can no longer reach the events table through any path
- [x] Existing 1970-looking rows read back in 2026 after schema-ensure
- [x] Suite green; PR open
