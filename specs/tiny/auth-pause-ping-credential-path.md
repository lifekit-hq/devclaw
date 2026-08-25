# TinySpec: auth-pause ping names the credential path it actually reads

**Issue**: #569
**Branch**: goal/devclaw-auth-ping-path-2026-08-25
**Date**: 2026-08-25
**Status**: done
**Complexity**: small

## What

The auth-pause owner ping says "re-login" without saying **where**. The process
that sends the ping already knows: `DEVCLAW_HOST_CLAUDE_DIR` is in its env and
is exactly the path a re-login must target. The message should name it, state
that a login elsewhere changes nothing, and give the verification step
(`dry_evaluate`).

## Context

| File | Role |
|------|------|
| `devclaw/goal/tick.py` | Will be modified — auth-pause ping message (~:764) |
| `tests/test_goal_rate_limit.py` | Will be modified — update one assertion + add named regression test |

Background: on 2026-08-19 the operator re-logged as `root` on the VPS host;
the container reads `/home/lifekit/.claude` (compose line 98), so cognition
stayed dead through a "completed" re-login and the outage extended by ~1h.

## Requirements

1. The auth-pause ping contains the configured `DEVCLAW_HOST_CLAUDE_DIR`
   value plus `/.credentials.json` — the exact file a re-login must land in.
2. The ping contains `setup-token` (the fix verb) and `dry_evaluate`
   (the verification step).
3. The ping states that a login landing elsewhere changes nothing (the
   wrong-home trap of 2026-08-19).
4. Pause/resume mechanics are byte-unchanged — existing auth/quota-pause
   tests stay green; the change is message-only.
5. Named regression test: `test_auth_pause_ping_names_the_credential_path`.

## Plan

1. `devclaw/goal/tick.py`: add `from .. import config as _config` and rewrite
   the auth-pause message at ~:764 to include the credential path, wrong-home
   trap, `setup-token`, and `dry_evaluate`.
2. `tests/test_goal_rate_limit.py`: update the one existing assertion that
   checks for `"/login"` (now use `"setup-token"`); add the named regression
   test using `monkeypatch.setenv("DEVCLAW_HOST_CLAUDE_DIR", ...)`.

## Tasks

- [x] Add `_config` import to `tick.py`
- [x] Rewrite auth-pause message in `tick.py`
- [x] Update existing test assertion (`/login` → `setup-token`)
- [x] Add `test_auth_pause_ping_names_the_credential_path`
- [x] Full suite + `ruff check .` + `mypy` green

## Done When

- [x] All tasks checked off
- [x] The rendered auth-pause ping contains the configured
      `DEVCLAW_HOST_CLAUDE_DIR` value, `.credentials.json`, `setup-token`,
      `dry_evaluate`, and the wrong-home statement
- [x] Existing pause/resume tests stay green (message-only change)
