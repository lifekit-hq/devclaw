# TinySpec: the host-cognition cap is a live operator dial too

**Branch**: feat/live-host-cognition-dial
**Date**: 2026-08-31
**Status**: done
**Complexity**: small

## What

Give `DEVCLAW_MAX_HOST_COGNITION` the same treatment the task cap just got:
`set_max_host_cognition` changes the concurrent host-side `claude --print`
ceiling on a running instance, no restart and no redeploy.

Completes the pair. `set_max_concurrent` caps SANDBOXED TASKS; this caps HOST
SUBPROCESSES. Neither population is visible to the other and both apply at
once — capping tasks at 1 does not stop two done-gates running beside that
task, which is exactly the surprise that motivated finishing the job.

## Context

The task cap could simply be re-read per pump. This one could not: the gate is
an `asyncio.Semaphore` whose capacity is fixed at construction.

**Rebuilding the semaphore on change is not a valid substitute.** Holders of
the old object release into the old object, so a fresh one starts at full
capacity while those calls are still running and the host briefly admits MORE
than the cap. That over-admission is precisely what OOM-killed 117 cognition
calls (`exited -9`) and put this gate here in the first place. So the fix
counts in-flight itself and re-checks the cap on every acquire.

| File | Role |
|------|------|
| `devclaw/llm_call.py` | Modified — `_DynamicLimiter` replaces the fixed semaphore; `set_host_cognition_cap` / `host_cognition_cap` module globals |
| `devclaw/state_store/control.py` | Modified — `set_max_host_cognition` / `max_host_cognition`, sibling of the task dial |
| `devclaw/server/lifecycle.py` | Modified — `_seed_host_cognition_cap()` pushes the stored value into `llm_call` at start |
| `devclaw/server/tools/control.py` | Modified — the tool; `get_run_schedule` reports both caps in one shape |
| `devclaw/server/tools/__init__.py` | Modified — re-export |
| `tests/test_host_cognition_semaphore.py` | Extended — live-dial + over-admission cases on the EXISTING class test |
| `tests/test_live_concurrency_dial.py` | Extended — store cases parametrized over both dials |

## Requirements

1. `set_max_host_cognition(n)` changes the effective cap on the next acquire.
2. `null` clears the override; the cap returns to `DEVCLAW_MAX_HOST_COGNITION`.
3. `n < 1` rejected at tool, store, and setter — `0` would deadlock every call.
4. **Lowering the cap never over-admits**: with N in flight and the cap dropped
   below N, no new call is admitted until in-flight falls under the new cap.
5. Lowering never cancels in-flight cognition; raising takes effect on the next
   release (a waiter only exists while something is in flight, so that wait is
   bounded by one call — never a deadlock).
6. The override survives a restart: seeded from the control plane at serve.
7. `llm_call` does NOT import the state store — layer 3 stays a primitive; the
   durable value is pushed in from layer 1.

## Plan

Swap the semaphore for a condition-based limiter that re-reads the cap per
acquire, add the store pair, seed at startup, expose the tool, and extend the
two existing class tests rather than minting siblings.

## Tasks

- [x] `_DynamicLimiter` + cap accessors in `llm_call.py`
- [x] `set_max_host_cognition` / `max_host_cognition` in the control store
- [x] `_seed_host_cognition_cap()` in server lifecycle
- [x] `set_max_host_cognition` tool + `get_run_schedule` reporting both caps
- [x] Re-export
- [x] Extend `test_host_cognition_semaphore.py` (live dial + over-admission)
- [x] Parametrize `test_live_concurrency_dial.py` store cases over both dials
- [x] env-vars row + INDEX currency tag
- [x] Full suite + `ruff check .` + `mypy` green

## Done when

- `set_max_host_cognition(1)` serializes host cognition immediately.
- Dropping the cap below the in-flight count admits nothing new until it drains.
- The override is still in force after a restart.
- No accepted value can deadlock cognition.
