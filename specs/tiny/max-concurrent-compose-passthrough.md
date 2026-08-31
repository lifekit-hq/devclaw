# TinySpec: `DEVCLAW_MAX_CONCURRENT` reaches the container

**Branch**: fix/max-concurrent-compose-passthrough
**Date**: 2026-08-31
**Status**: done
**Complexity**: small

## What

Add the compose substitution line for `DEVCLAW_MAX_CONCURRENT` so the global
sandboxed-task concurrency cap can actually be set by the operator. Today
`devclaw/config.py` reads it (`int(os.environ.get("DEVCLAW_MAX_CONCURRENT",
"4"))`) and `task_queue.py` enforces it, but the devclaw-mcp `environment:`
block never passes it through — so a line in `/srv/devclaw/.env` is a SILENT
no-op and the cap is pinned at its default of 4 forever.

This is the identical failure class the sandbox-sizing knobs hit on
2026-08-26, and the warning comment sitting directly above those lines in the
same file states it: *"Without these substitution lines the knobs never reach
the container and setting them in the env file is a silent no-op."* The knob
was simply never added when the OOM incident fixed its neighbours.

Motivating need (Denys, 2026-08-31): a week of unattended operation wants
STRICTLY SERIAL execution — one sandbox at a time, 24/7. At the pinned default
of 4, concurrent sandboxes contend for the same account quota; on 2026-08-29
that produced six tasks each dying at `exceeded 5 usage-limit pauses` with
zero delivered increments. Serial is not merely tidier, it is more reliable:
the pause budget goes to one job instead of being split four ways.

## Context

| File | Role |
|------|------|
| `deploy/docker-compose.devclaw.yml` | Modified — `DEVCLAW_MAX_CONCURRENT: ${DEVCLAW_MAX_CONCURRENT:-4}` in the devclaw-mcp `environment:` block, beside the sandbox-sizing knobs it belongs with |
| `docs/reference/env-vars.md` | Modified — the existing row gains the pass-through fact and the serial-operation note |
| `docs/INDEX.md` | Modified — currency tag |
| `devclaw/config.py` | UNCHANGED — already the single doorway; default stays 4 |
| `devclaw/task_queue.py` | UNCHANGED — already enforces the cap |

## Requirements

1. `DEVCLAW_MAX_CONCURRENT` set in `/srv/devclaw/.env` reaches the devclaw-mcp
   container and changes the effective cap.
2. Unset ⇒ `4`, byte-identical to today's behavior. The default is expressed
   once in compose (`:-4`) mirroring `config.py`, exactly as the sandbox-sizing
   knobs do.
3. No code change: `config.py` remains the single doorway and `task_queue.py`
   remains the single enforcement point.
4. `DEVCLAW_MAX_CONCURRENT=1` yields strictly serial sandboxed execution.

## Plan

Add one substitution line to the compose `environment:` block, grouped with the
sandbox-sizing knobs under the comment that already explains why the line is
required. Amend the env-vars doc row and its INDEX currency tag.

## Tasks

- [x] Add `DEVCLAW_MAX_CONCURRENT: ${DEVCLAW_MAX_CONCURRENT:-4}` to
      `deploy/docker-compose.devclaw.yml`
- [x] Amend the `DEVCLAW_MAX_CONCURRENT` row in `docs/reference/env-vars.md`
- [x] Update the `docs/reference/env-vars.md` currency tag in `docs/INDEX.md`
- [x] `pytest` + `ruff check .` + `mypy` green

## Done when

- A `DEVCLAW_MAX_CONCURRENT` line in the VPS env file changes the running
  instance's effective cap after a redeploy.
- Removing the line restores the default of 4 with no behavior change.
- No test ships: this touches no tripwire class (it is a deployment
  pass-through, and `test_env_vars_doc_sync` already guards doc↔code parity
  for the var itself).
