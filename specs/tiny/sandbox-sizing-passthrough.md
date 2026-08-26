# Sandbox sizing knobs must reach the deployed container

## What

Pass `DEVCLAW_SANDBOX_MEMORY`, `DEVCLAW_SANDBOX_CPUS`, and
`DEVCLAW_COGNITION_MEM_RESERVE` through `deploy/docker-compose.devclaw.yml`
into the devclaw-mcp container, so an operator setting them in
`/srv/devclaw/.env` actually changes the running instance.

## Context

2026-08-26 daytime incident: goal `lkc-type-rollup-2026-08-26` failed twice
with "The Claude Agent process exited unexpectedly" and dispatch-cap-blocked.
Root cause: lifekit-common's `ng test` run does not fit in the sandbox's
default 2g memory cap alongside the in-sandbox claude agent; the cgroup OOM
killer took the agent process. The operator remedy documented in
`docs/reference/env-vars.md` is `DEVCLAW_SANDBOX_MEMORY` — but compose
`--env-file` only substitutes into the compose file, and the fragment's
`environment:` block never referenced the sizing knobs, so setting them in
`/srv/devclaw/.env` was silently a no-op. The knob existed in code and docs
but was unreachable on a deployed instance.

## Requirements

- Each of the three sizing knobs is substituted into the devclaw-mcp
  `environment:` block as `${VAR:-<code default>}` — defaults identical to
  `devclaw/config.py` so an unset knob changes nothing.
- `deploy/.env.example` advertises the knobs in the operator-knobs section.
- A named regression test pins the passthrough (the stubbed suite cannot see
  a deployed box; the structural pin is asserting the fragment text — #641's
  drift class).

## Plan

One commit: compose fragment + .env.example + test.

## Tasks

- [x] Add the three `${VAR:-default}` lines to the devclaw-mcp environment block
- [x] Add commented knob lines to deploy/.env.example
- [x] Named test: `tests/test_deploy_compose.py::test_sandbox_sizing_knobs_are_passed_through_to_devclaw_mcp`

## Done-When

Setting `DEVCLAW_SANDBOX_MEMORY=4g` in `/srv/devclaw/.env` and recreating
devclaw-mcp yields `docker exec devclaw-mcp env | grep DEVCLAW_SANDBOX_MEMORY`
→ `4g`, and subsequent sandbox containers run with `--memory 4g`.

## Rejected alternatives

- Hand-editing the compose fragment on the box: drift against the checkout;
  the next deploy silently reverts it.
- A generic `env_file:` on the service: leaks every host fact in
  `/srv/devclaw/.env` (LIFEKIT_* paths, tokens) into the container env;
  explicit substitution keeps the container env intentional.
- Also passing `DEVCLAW_GOAL_AUTOMERGE` (advertised in .env.example, never
  passed): the variable no longer exists in code post-008-shrink — its fate
  belongs to issue #641, not this fix.
