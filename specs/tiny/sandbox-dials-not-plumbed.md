# TinySpec: the sandbox dials are documented, settable, and silently inert

**Branch**: fix/exec-model-not-plumbed
**Date**: 2026-09-02
**Status**: done
**Complexity**: small

## What

`deploy/docker-compose.devclaw.yml` has no `env_file:` — it forwards **only**
the vars it names explicitly. So a knob an operator sets in
`/srv/devclaw/.env` never reaches the process unless a line exists in that
`environment:` block.

Five dials that `devclaw/engine/sandcastle.py` acts on had no such line:

| var | consequence on the deployed instance |
|---|---|
| `DEVCLAW_EXEC_MODEL` | **the worker model cannot be changed at all** |
| `DEVCLAW_ACP_COMMAND` | the layer-5 agent-replaceability seam is unreachable |
| `DEVCLAW_CONTEXT_TRIPWIRE_PCT` | the context-budget threshold is unsettable |
| `DEVCLAW_DOCKER_BIN` | — |
| `DEVCLAW_SANDBOX_CLAUDE_ALLOWLIST` | the sandbox allowlist is unsettable |

`DEVCLAW_EXEC_MODEL` is the one that stung: `docs/reference/env-vars.md`
documents it as **"the in-sandbox coding agent — the token/quota bulk"**, the
single largest cost/behaviour dial in the system, and setting it on the
deployed instance did nothing. Found live 2026-09-02 while trying to move the
worker off its `claude-sonnet-4-6` default: the var was added to
`/srv/devclaw/.env`, the container recreated, and
`docker exec … env | grep EXEC_MODEL` returned nothing.

The trap was already known. The compose file carries this comment above
`DEVCLAW_MAX_CONCURRENT`:

> *"Same silent-no-op trap as the sizing knobs below: config.py reads it and
> task_queue.py enforces it, but without this line an env-file setting never
> reaches the container."*

Someone hit it, fixed that **one instance**, and left the class — the exact
failure mode `CLAUDE.md`'s "fix the class, not the instance" doctrine exists
to prevent.

## Context

| File | Role |
|------|------|
| `deploy/docker-compose.devclaw.yml` | Will be modified — forward the five missing dials |
| `devclaw/engine/sandcastle.py` | Context — the container launcher; every `_config.*` it reads is by definition a production-path dial |
| `devclaw/config.py` | Context — the env doorway each dial binds through |
| `tests/test_env_vars_doc_sync.py` | Will be modified — the env-parity class test gains the compose-forwarding axis |

## Requirements

1. Every `DEVCLAW_*` var `engine/sandcastle.py` acts on is forwarded by the
   production compose file, with a default mirroring `devclaw/config.py`.
2. The guard resolves the dial set **structurally** — the `_config.<NAME>`
   references in `sandcastle.py`, mapped back through `config.py` to their env
   var — never a hand-kept list. A new dial is covered the moment sandcastle
   reads it.
3. The guard fails with an actionable message naming the unforwarded vars.
4. It fails LOUD if its own structural scan finds nothing (a rotted regex must
   not read as "all clear").
5. No behaviour change to defaults: every added line defaults to exactly what
   `config.py` already defaults to, so an instance that sets nothing is
   byte-unaffected.

## Plan

1. Add the five `DEVCLAW_*` lines to the compose `environment:` block, beside
   the existing sizing knobs, with a comment naming the trap.
2. Extend `tests/test_env_vars_doc_sync.py` — it already owns
   docs ↔ code env parity; the deployed-container axis belongs in the same
   class test, not a sibling file.

## Tasks

- [x] Forward `DEVCLAW_EXEC_MODEL`, `DEVCLAW_ACP_COMMAND`,
      `DEVCLAW_CONTEXT_TRIPWIRE_PCT`, `DEVCLAW_DOCKER_BIN`,
      `DEVCLAW_SANDBOX_CLAUDE_ALLOWLIST` in the compose file
- [x] `_sandcastle_env_vars()` — structural resolution of the dial set
- [x] `test_every_sandbox_dial_reaches_the_deployed_container` — verified to
      resolve 11 dials and to FAIL naming `DEVCLAW_EXEC_MODEL` when its line
      is removed
- [x] Full suite + `ruff check .` + `mypy` green

## Done When

- [x] All tasks checked off
- [x] `DEVCLAW_EXEC_MODEL` set in `/srv/devclaw/.env` reaches the container
      after a deploy (verify: `docker exec … env | grep EXEC_MODEL`)
- [x] A future dial added to `sandcastle.py` without a compose line fails the
      suite instead of shipping inert
