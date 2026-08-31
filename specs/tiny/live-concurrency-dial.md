# TinySpec: the concurrency cap is a live operator dial

**Branch**: feat/live-concurrency-dial
**Date**: 2026-08-31
**Status**: done
**Complexity**: small

## What

Make the global sandboxed-task cap changeable on a RUNNING instance:
`set_max_concurrent` writes a control-plane override the queue resolves once
per pump, so backpressure can be tuned without a restart, a redeploy, or SSH.

## Context

`DEVCLAW_MAX_CONCURRENT` was read once at import by `config.py` and captured in
a module constant. Changing 4 → 1 therefore required: edit `/srv/devclaw/.env`
(root-owned, sudo), open a PR to add the compose pass-through line that never
existed, merge it, redeploy. Four steps and two privilege boundaries to change
one integer that the queue re-reads on every pump anyway.

That is the wrong CLASS for this knob. Every other operator control —
`set_operator_hold`, `set_run_schedule`, `set_quiet_mode`,
`set_goal_strictness` — already lives in the control plane and takes effect
immediately. Spec 020 made exactly this move for `sandbox_memory` /
`sandbox_cpus` (registry overrides beat instance defaults). The concurrency cap
simply never got the same treatment.

| File | Role |
|------|------|
| `devclaw/state_store/control.py` | Modified — `set_max_concurrent` / `max_concurrent` beside the operator hold, same meta-key conventions |
| `devclaw/task_queue.py` | Modified — `_effective_max_concurrent()` resolved per pump; `GLOBAL_MAX_CONCURRENT` demoted to the default |
| `devclaw/server/tools/control.py` | Modified — `set_max_concurrent` tool; `get_run_schedule` surfaces effective/override/default |
| `devclaw/server/tools/__init__.py` | Modified — re-export (guarded by `test_tools_reexport_complete`) |
| `tests/test_live_concurrency_dial.py` | Added — the brake-machinery tripwire |
| `docs/reference/env-vars.md`, `docs/INDEX.md` | Modified — the var is now the default/floor |

## Requirements

1. `set_max_concurrent(n)` changes the effective cap on the next queue pump,
   with no restart and no redeploy.
2. `set_max_concurrent(null)` clears the override; the cap returns to
   `DEVCLAW_MAX_CONCURRENT`. Absence is the signal — never a stored copy of the
   default — so changing the env var still moves the floor for an instance that
   never set an override.
3. The dial is BACKPRESSURE, not a safety gate: `n < 1` is rejected at both the
   tool and the store. It can never wedge dispatch to zero — halting work is
   `set_operator_hold`, and gating by time is `set_run_schedule`.
4. A corrupt stored value or a store read failure degrades to the default,
   never to 0.
5. In-flight tasks are never killed by lowering the cap; only new launches wait.
6. `get_run_schedule` reports `{effective, override, default}`.

## Plan

Add the store pair, resolve the cap per pump behind a helper, expose the tool,
surface it on the existing read tool, and pin the invariant with one tripwire
test covering both halves (it takes effect live; it can never reach zero).

## Tasks

- [x] `set_max_concurrent` / `max_concurrent` in `state_store/control.py`
- [x] `_effective_max_concurrent()` + per-pump resolution in `task_queue.py`
- [x] `set_max_concurrent` MCP tool + `get_run_schedule` surfacing
- [x] Re-export in `server/tools/__init__.py`
- [x] `tests/test_live_concurrency_dial.py`
- [x] env-vars row + INDEX currency tag
- [x] Full suite + `ruff check .` + `mypy` green

## Done when

- `set_max_concurrent(1)` makes the next pump launch at most one sandbox, with
  no restart.
- Clearing the override restores the `DEVCLAW_MAX_CONCURRENT` default.
- No value accepted by the tool or the store can stop dispatch entirely.
