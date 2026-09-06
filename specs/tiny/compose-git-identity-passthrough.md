# TinySpec: DEVCLAW_GIT_NAME / DEVCLAW_GIT_EMAIL reach the deployed container

**Issue**: none (Denys, 2026-09-06 — "act with own name" on GitHub)
**Branch**: fix/compose-git-identity-passthrough
**Date**: 2026-09-06
**Status**: done
**Complexity**: small

## What

`DEVCLAW_GIT_NAME` and `DEVCLAW_GIT_EMAIL` are documented in
`docs/reference/env-vars.md`, read by `devclaw/config.py`, and stamped on
every commit by `devclaw/git_identity.py` — but the production compose file
never forwarded them, so setting them in `/srv/devclaw/.env` was a silent
no-op. This is the same "documented, settable, inert" trap the compose file
already calls out for `DEVCLAW_MAX_CONCURRENT` and the sandcastle dials, and
the class guard missed it because the launcher reads the identity THROUGH
`git_identity.py`, one hop past the file the guard scans.

## Context

| File | Role |
|------|------|
| `deploy/docker-compose.devclaw.yml` | Forward the two vars (blank passthrough, like `DEVCLAW_EXEC_MODEL`) |
| `devclaw/git_identity.py` | Blank ⇒ default, so a `${VAR:-}` passthrough can never author `"" <"">` |
| `tests/test_env_vars_doc_sync.py` | The class guard scans `git_identity.py` alongside `sandcastle.py` |
| `deploy/.env.example` | The two knobs listed under operator knobs |

Direction memory: the separate GitHub identity itself is deferred — Denys does
not want a second account now. When it comes, it is an org-owned GitHub App
(no account, no 2FA; needs a token-minting seam behind `procutil.run` — a
spec), or a machine user (login swap only). Either way the commit link is then
ONE env change: `DEVCLAW_GIT_EMAIL=<id>+<name>@users.noreply.github.com`.
Rejected for now: a compose default of `devclaw`/`devclaw@local` (a second
home for the default; the code owns it).

## Requirements

1. A value set on the box for either var reaches the container and every
   sandbox it launches.
2. An unset/blank value keeps the code default `devclaw <devclaw@local>`.
3. The structural guard fails if either var stops being forwarded.

## Plan / Tasks

- [x] compose: forward `DEVCLAW_GIT_NAME` / `DEVCLAW_GIT_EMAIL` as blank passthroughs
- [x] `git_identity.py`: treat blank as unset
- [x] guard: include `git_identity.py` in the sandbox-dial scan (proved red without the compose lines, green with)
- [x] `.env.example`: document the knobs

## Done-When

`test_every_sandbox_dial_reaches_the_deployed_container` is red with the
compose lines removed and green with them; a hand `DEVCLAW_GIT_EMAIL=...` on
the box shows up as the author email of the next delivered commit.
