# Tiny spec — a merge to main redeploys, once nothing is running

## North-star case

- **Failure moved**: *ran but needed the owner.* Deploying was an owner
  button-press, so every merge left the instance running old code until Denys
  remembered. The owner acts only on decisions (2026-09-07); "go press deploy"
  is not one.
- **Number**: deploy lag — merges sitting on `main` that the running instance
  has not picked up. On 2026-09-09 the box sat **5 merges behind** (`cce14bc`
  vs `7bb6714`) including two credential fixes and a console fix, and only
  moved because it was noticed by hand. Secondary: `interventions` per
  achieved goal, which counts every owner action a change adds.
- **Cut when**: if an armed deploy ever recreates the instance while a task is
  running — one killed sandbox is enough — the arming lane is reverted to
  devclaw's own merge-on-close and deploying stays a button.

## What

**Amends spec 005 FR-008** ("a merge MUST NOT automatically rebuild-and-recreate
the live instance; deploy MUST be operator-triggered"), which spec 025 US2
already amended once for devclaw's own merge-on-close. Ruled by Denys
2026-09-09: *"I want deploy to be part of the CI/CD pipeline. When we merge to
main it redeploys. The only thing — I don't want it to interrupt an active run."*

Every push to `main` **arms** a self-deploy. The instance decides when to run
it. Nothing about the deploy itself changes.

## Context

The hard part was already built and is not rebuilt here. Spec 025 US2 gave the
instance `deploy_pending` → `self_deploy.maybe_trigger` on the heartbeat →
waits for `count_running() == 0` → fires `deploy.yml`'s auto lane, which
probes and rolls back once. **The only gap was what arms it**: the sole caller
of `set_deploy_pending` was merge-on-close for a devclaw-repo goal, so a merge
by anyone else — a human, release-please, dependabot automerge — armed nothing
and was invisible until somebody looked.

So this is a repointing, not a third deploy path. Three things exist at this
boundary already (manual lane, auto lane, `maybe_trigger`); adding a fourth
would be the smell. What lands is one route and one workflow job.

**Where the quiescence gate lives, and why not on the runner.** The obvious
shape — `on: push` runs the deploy — cannot satisfy the second half of the
ruling: GitHub does not know whether a sandbox is mid-run. Checking on the
runner would put a second gate on the same question with worse information, and
any disagreement between the two is a killed sandbox. So the split is:
**GitHub states a fact ("main is at <sha>"), the instance owns the decision.**

**Rejected — the instance polls main.** The heartbeat could compare its own
`git_sha` against the remote and self-arm. It works, and it needs no inbound
call — but it adds a periodic `git ls-remote` to a tick path whose whole design
is that idle costs nothing, and it discovers a merge up to a heartbeat late for
no gain. The runner already sits on the VPS with loopback access; a push is
free and exact.

**Rejected — a new Actions secret for the token.** The arm call needs the
instance's bearer token. Copying `DEVCLAW_TOKEN` into GitHub creates a
credential hop that must then be declared, scoped and maintained (spec 042).
The runner is *on the box*, so it reads the token from the env file the deploy
scripts already read and it never travels. Same reasoning as #894.

**Not solved here — quiescence that never arrives.** `maybe_trigger` expires a
pending deploy after `DEVCLAW_DEPLOY_QUIESCENCE_S` (6h). Under the old arming
path that was fine: rare, and re-armed by the next close. Now that every merge
arms one, an expiry means a merge silently never ships. This spec makes the
expiry **loud** (an owner ping instead of a stderr line) but does not add a
drain — stopping new dispatch so running work can finish and the deploy can
proceed. A drain is a fourth thing gating dispatch (operator hold, quota pause,
run window, …) and trades "the loop never pauses" against "the box is always
current". That is a direction call, not a detail, and it is left for Denys with
the ping as the visible prompt.

## Requirements

- **FR-001** A push to `main` arms a self-deploy with that sha. No deploy runs
  on the push event.
- **FR-002** An armed deploy fires only when `count_running() == 0`. A running
  task holds it — indefinitely, up to the existing expiry.
- **FR-003** The `workflow_dispatch` lanes (manual, and `auto=true`) are
  byte-unchanged; a deliberate operator deploy behaves exactly as before.
- **FR-004** Arming is idempotent — a newer merge overwrites the sha, since
  deploying the latest main covers every merge behind it.
- **FR-005** The arm call authenticates with a token read from the box, never
  from an Actions secret; no new credential crosses a new hop.
- **FR-006** A failed arm fails the workflow loudly and says main is ahead of
  the running instance.
- **FR-007** An expired or failed self-deploy pings the owner. A merge that
  never reached production must not be a stderr line.
- **FR-008** The idle path is unchanged: nothing armed ⇒ one meta read, no
  subprocess, no cognition.

## Plan

1. `devclaw/server/routes/control.py` — `POST /control/deploy-pending`.
2. `.github/workflows/deploy.yml` — `on: push: [main]` with an `arm` job; the
   `deploy` job gated to `workflow_dispatch`.
3. `devclaw/goal/service.py` — the heartbeat pings on `expired` /
   `trigger_failed`.
4. `specs/005-devclaw-self-deploy/spec.md` — FR-008 marked amended.
5. Tripwire test: an armed deploy never fires while a task runs.

## Tasks

- [x] T1 arm route (FR-001, FR-004, FR-005)
- [x] T2 workflow arm lane; deploy lane gated to dispatch (FR-001, FR-003, FR-006)
- [x] T3 expiry/failure pings the owner (FR-007)
- [x] T4 tripwire test — never interrupts a run (FR-002, FR-008)
- [x] T5 amend spec 005 FR-008 in place

## Done when

- Merging to main leaves the instance on the new sha without anyone pressing
  anything, once the running task finishes.
- A merge during an active run does not kill it.
- `workflow_dispatch` still deploys immediately on demand.
- `pytest`, `ruff check .`, `mypy`, `lint-imports` clean.
