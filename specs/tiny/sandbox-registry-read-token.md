# TinySpec: Registry-read token for sandbox npm installs

**Branch**: feat/sandbox-registry-read-token
**Date**: 2026-08-28
**Status**: done
**Complexity**: small

## What

Pass a `read:packages`-scoped `NODE_AUTH_TOKEN` into the per-task sandbox so
workers on `@lifekit-hq`-consuming repos (finance-sentry frontend, future
web-component consumers) can run a real `npm ci` and real-app Playwright e2e.
Today the sandbox carries no registry credential, so `frontend/.npmrc`
(`//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}`) can never resolve the
four `@lifekit-hq/*` packages; workers fall back to fixture-level e2e the
done-gate rightly rejects (fs-479 clause-3 gap, night run 2026-08-27). Rides
the exact `CLAUDE_CODE_OAUTH_TOKEN` pattern: repo secret → deploy workflow
env → compose shell-env resolution → one `-e` into the sandbox; absent/blank
⇒ no `-e` at all; never written to disk on the VPS.

## Context

| File | Role |
|------|------|
| `devclaw/engine/sandcastle.py` | Modified — `REGISTRY_TOKEN_VAR = "NODE_AUTH_TOKEN"` + `_registry_token_env()` beside `_oauth_token_env()` (l.155); wired into the `docker run` args beside it (l.579) |
| `deploy/docker-compose.devclaw.yml` | Modified — `NODE_AUTH_TOKEN: ${NODE_AUTH_TOKEN:-}` in the devclaw-mcp `environment:` block (beside l.71) |
| `.github/workflows/deploy.yml` | Modified — `NODE_AUTH_TOKEN: ${{ secrets.NODE_AUTH_TOKEN }}` in the deploy step `env:` |
| `tests/test_sandbox_isolation.py` | Modified — extend the existing env-crossing tests (l.533–583), not new siblings: forwarded when set, absent `-e` when unset/blank, metered keys still stripped |
| `docs/reference/env-vars.md` + `docs/INDEX.md` | Modified — new row mirroring the `CLAUDE_CODE_OAUTH_TOKEN` row; currency tag |

## Requirements

1. When the host process env carries a non-blank `NODE_AUTH_TOKEN`, every
   sandbox `docker run` includes exactly `-e NODE_AUTH_TOKEN=<value>`.
2. Unset or blank ⇒ no `-e` for it at all (never `NODE_AUTH_TOKEN=`) — the
   pre-token deployment keeps working byte-identically.
3. The OAuth-only invariant is untouched: `ANTHROPIC_API_KEY` /
   `ANTHROPIC_AUTH_TOKEN` remain stripped on the same path.
4. The secret's home is the repo's `NODE_AUTH_TOKEN` Actions secret; compose
   resolves it from the deploy step's shell env — no line required in
   `/srv/devclaw/.env` (env-file line stays as operator fallback), nothing
   written to disk.
5. Token scope is `read:packages` ONLY — the sandbox still holds no
   credential that can push, merge, or touch issues/PRs; delivery ceremony
   stays host-side. (Operator-enforced at token creation; the spec records
   the boundary.)
6. `DEVCLAW_ENGINE=host` needs no change — the runner inherits the process
   env there already.

## Plan

1. `sandcastle.py`: add `REGISTRY_TOKEN_VAR` + `_registry_token_env()`
   (copy of `_oauth_token_env()` shape), splice into the run-args tuple.
2. Compose fragment + deploy workflow: one line each, mirroring the OAuth
   token's plumbing.
3. Extend the named env-crossing tests in `test_sandbox_isolation.py` with
   the registry-token cases.
4. Docs: `env-vars.md` row + INDEX currency tag.

## Tasks

- [x] `_registry_token_env()` in sandcastle.py, wired into the docker run args
- [x] `NODE_AUTH_TOKEN: ${NODE_AUTH_TOKEN:-}` in docker-compose.devclaw.yml
- [x] `NODE_AUTH_TOKEN` secret → deploy step env in deploy.yml
- [x] Tests: forwarded / absent-when-unset-or-blank / metered-keys-still-stripped
- [x] Docs row + INDEX tag
- [x] **Denys (manual)**: create a `read:packages`-only token, add it as the
      `NODE_AUTH_TOKEN` Actions secret on lifekit-hq/devclaw, redeploy

## Done When

- [x] All tasks checked off; full suite + ruff + mypy green
- [ ] A live sandbox on finance-sentry resolves `@lifekit-hq/*` via `npm ci`
      (proven on the next fs frontend dispatch after redeploy)
