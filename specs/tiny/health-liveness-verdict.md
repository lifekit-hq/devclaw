# health-liveness-verdict — /health renders a staleness verdict an external observer can trust

## What

`/health` returns `ok: true` unconditionally whenever uvicorn answers —
even when the heartbeat freshness stamp (`last_tick_at`) is arbitrarily
stale. The stamp exists deliberately as "the signal an external dead-man
watcher needs" (`goal/service.py`, #494), but nothing renders a verdict
from it: the compose healthcheck only proves the HTTP process is alive,
and the ops-agent watchdog has no field it can act on. Add the verdict:
`stale: true` + a reason naming the true failing condition when the loop
has stopped completing passes.

## Context

Audit finding G2 / borrow B3(a), agreed by Denys 2026-09-05 ("Agree
now"). Ground truth in the code makes the verdict clean:

- `tick_all` stamps `last_tick_at_ms` only on a COMPLETED pass; a
  perpetually-crashing tick leaves it stale (`goal/service.py`).
- The quota pause is an early **return inside** `tick_all`, and the
  operator hold / run-window gate dispatch inside the tick — the
  heartbeat loop keeps running and stamping through all of them. So a
  stale stamp means the loop is wedged or dead, NEVER "held on purpose";
  dispatch-held is already distinctly surfaced (`dispatch_open` /
  `dispatch_hold_reason`, O3). An alarm that named the wrong condition
  would train its reader to distrust it.
- Back-compat: `/health` has existing consumers (compose healthcheck
  curl, ops-agent poll). Top-level `ok` keeps meaning "HTTP process
  serving"; the verdict rides new fields, and exit-code semantics are an
  explicit opt-in (`?strict=1` → 503 when stale) so nothing restart-loops
  on today's shape.

## Requirements

- `/health` (and `/node.json`'s shared `freshness` block) carries
  `stale: bool`, `stale_reason: str | null`. All existing fields keep
  their shape.
- `stale_reason` names the true condition distinctly:
  `heartbeat_stale` (the loop ticked once but the stamp now exceeds the
  threshold) vs `loop_never_ticked` (process start never completed a
  first pass — startup recovery hung or the loop never started).
- Threshold = `DEVCLAW_HEALTH_STALE_TICKS` (default `3`) ×
  `tick_seconds`; configured through `devclaw/config.py` (the single
  doorway) and documented in `docs/reference/env-vars.md` (the doc-sync
  test enforces the pair).
- A held/paused-but-ticking instance is NOT stale; unknown inputs
  (missing `tick_seconds` / `started_at`) render no alarm — unknown is
  not an alarm.
- `GET /health?strict=1` returns 503 + `ok: false` when stale, so a
  curl-based check gets exit-code semantics by opting in.
- Verdict logic unit-tested: fresh, stale, never-ticked past grace,
  never-ticked within grace, dispatch-held-but-fresh. No new processes,
  no LLM calls.

## Plan

Pure function `_liveness_verdict(...)` in
`devclaw/server/routes/control.py`; `_health_freshness()` calls it and
adds the three fields (one truth for `/health` + `/node.json`); the
`/health` route reads `strict` from the query string. New
`config.health_stale_ticks()` beside the other `DEVCLAW_HEALTH_*` knobs.
Any compose-healthcheck upgrade to `?strict=1` belongs to lifekit-stack
— follow-up, not this repo.

## Tasks

- [x] `devclaw/config.py`: `health_stale_ticks()` — `DEVCLAW_HEALTH_STALE_TICKS`, default 3, non-positive/unparseable → 3.
- [x] `devclaw/server/routes/control.py`: `_liveness_verdict` + fields in `_health_freshness`; `?strict=1` handling in `health`.
- [x] `docs/reference/env-vars.md`: row in the instance-health group; `docs/INDEX.md` currency tag.
- [x] `tests/test_health_verdict.py`: the five verdict cases + strict-mode status codes.

## Done when

`/health` on a wedged instance (stamp older than N × tick interval)
answers `stale: true` with the right reason while a merely-held instance
stays `stale: false`; `?strict=1` turns that into a 503; the suite,
`ruff`, and `mypy` stay green.
