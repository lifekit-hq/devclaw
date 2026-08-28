# TinySpec: Playwright system deps baked into the sandbox image

**Branch**: fix/sandbox-playwright-sysdeps
**Date**: 2026-08-28
**Status**: done
**Complexity**: small

## What

Install Chromium's system libraries into the sandbox image
(`npx playwright install-deps chromium`, root stage) so an in-sandbox
Playwright run can actually launch the browser. The image already pre-fetches
the Chromium *binary* but never installed its *system deps* — the fs-479
worker had to hand-extract `libXfixes.so.3` into `/tmp` and the hack is now
baked into finance-sentry's committed `playwright.config.ts` as an
`LD_LIBRARY_PATH` workaround. With deps in the image, real-browser verify
runs (finance-sentry#481's `verifyCmd`) work without per-repo scars.

## Context

| File | Role |
|------|------|
| `.sandcastle/Dockerfile` | Modified — add root-stage `RUN npx -y playwright@latest install-deps chromium` (+ apt-list cleanup) before `USER agent`; the existing agent-stage browser prefetch stays |

## Requirements

1. The image build installs Chromium's apt dependencies as root (the base is
   guaranteed Debian — the Dockerfile fails loud on non-debian bases).
2. The existing `USER agent` browser prefetch (`playwright install chromium`)
   is unchanged — binary in the agent cache, deps in the system layer.
3. No test change: the pytest suite is fully stubbed (no docker); image
   content is proven by the live pipeline, not the suite. The symmetric
   ratchet is untouched (no behavior removed).

## Plan

1. Insert the `install-deps` RUN in the root stage, right before `USER agent`,
   with `rm -rf /var/lib/apt/lists/*` cleanup.

## Tasks

- [x] `install-deps chromium` root-stage RUN in `.sandcastle/Dockerfile`
- [ ] Image builds clean (deploy workflow is the build proof)

## Done When

- [ ] PR merged; next deploy rebuilds the sandbox image
- [ ] A live in-sandbox `npx playwright test` launches Chromium without
      `LD_LIBRARY_PATH` hacks (proven on the next fs frontend dispatch)
