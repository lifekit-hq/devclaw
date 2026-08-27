# TinySpec: verify_cmd overridable after goal creation (issue #711)

**Issue**: #711
**Branch**: goal/devclaw-auth-ping-path-2026-08-25
**Date**: 2026-08-27
**Status**: done
**Complexity**: small

## What

`verify_cmd` is set once at goal creation and can never change. If the correct
verification command for a repo changes (e.g. a new test suite is added, or the
initial value was wrong), the only recourse is to cancel + recreate the entire
goal. Add a `set_verify_cmd` service verb — the narrow single-field mutation
mirror of `set_strictness` — so the verification command is overridable without
a cancel/recreate cycle.

## Context

| File | Role |
|------|------|
| `devclaw/goal/store/base.py` | Add `set_verify_cmd()` — atomic goal.yaml patch |
| `devclaw/goal/service.py` | Add `set_verify_cmd()` — log + poke |
| `devclaw/server/tools/goals.py` | Add `set_goal_verify_cmd` MCP tool |
| `devclaw/server/routes/goals.py` | Add `POST /goals/{id}/verify_cmd` HTTP route |
| `tests/test_goal_store.py` | Store-level regression tests |
| `tests/test_console_goal_actions.py` | HTTP route regression tests |

The manifest tier is already read fresh at each dispatch (`_manifest_tiers`).
`goal.verify_cmd` shadows it; once the goal is created there is no way to
update the goal's value short of cancel + recreate.

## Requirements

1. `GoalStore.set_verify_cmd(goal_id, verify_cmd)` atomically rewrites
   `goal.yaml` to update only `verify_cmd` — all other fields preserved.
   Accepts `None` to clear (falling back to the manifest tier on next dispatch).
2. `GoalService.set_verify_cmd(goal_id, verify_cmd)` calls the store, logs
   `"verify_cmd set to <value>"`, pokes the heartbeat, returns
   `{"goal_id": ..., "verify_cmd": <new_value>}`.
3. MCP tool `set_goal_verify_cmd(goal_id, verify_cmd)` wraps the service.
   `verify_cmd=None` / empty string clears it. Raises `ToolError` on
   `KeyError` (unknown goal).
4. HTTP route `POST /goals/{goal_id}/verify_cmd` body
   `{"verify_cmd": "..."}` or `{"verify_cmd": null}` mirrors the MCP tool.
   Returns 400 on missing/extra bad body, 404 on unknown goal.
5. Named regression test:
   `test_set_verify_cmd_mutates_one_field_and_preserves_the_rest` (store).
   `test_verify_cmd_update_forwarded_to_service` (HTTP route).

## Plan

1. `devclaw/goal/store/base.py`: add `set_verify_cmd()` after `set_strictness()`.
2. `devclaw/goal/service.py`: add `set_verify_cmd()` after `set_strictness()`.
3. `devclaw/server/tools/goals.py`: add `set_goal_verify_cmd` MCP tool after
   `set_goal_strictness`.
4. `devclaw/server/routes/goals.py`: add HTTP route after `goal_strictness`.
5. `tests/test_goal_store.py`: add 2 tests (mutates-one-field, accepts-None).
6. `tests/test_console_goal_actions.py`: add 2 tests (forwards, 404).

## Tasks

- [x] Write tinyspec
- [x] Add `GoalStore.set_verify_cmd()`
- [x] Add `GoalService.set_verify_cmd()`
- [x] Add `set_goal_verify_cmd` MCP tool
- [x] Add `POST /goals/{id}/verify_cmd` HTTP route
- [x] Add regression tests (store + HTTP route)
- [x] Full suite + `ruff check .` + `mypy` green

## Done When

- [x] All tasks checked off
- [x] `set_verify_cmd` changes `verify_cmd` in goal.yaml, preserving all other fields
- [x] Clearing with `None` falls back to manifest tier on next dispatch
- [x] Named regression tests present and green
- [x] Full suite green, ruff + mypy clean
