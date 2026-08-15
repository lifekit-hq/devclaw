# Feature Specification: devclaw owns its own deployment

**Feature Branch**: `feat/devclaw-self-deploy`

**Created**: 2026-08-14

**Status**: Draft

**Input**: User description: decouple the devclaw runtime deployment from the lifekit-stack monolith so devclaw builds and ships its own deployable image(s) from its own source, with its own CI and deploy path, deployed independently of the OpenClaw/lifekit side.

## Clarifications

### Session 2026-08-15

- Q: Where is the devclaw image built/stored — locally on the VPS runner (no registry), or pushed to a registry (ghcr.io)? → A: **Registry (`ghcr.io/lifekit-hq/devclaw-mcp` + `devclaw-sandbox`)**. CI builds from the checked-out source and pushes SHA + `latest` tags; the deploy step pulls the tag and recreates. Decouples build host from run host and gives an off-box image archive (rollback pulls a prior SHA) at the cost of registry auth + a pull step. Supersedes the spec's earlier local-build-on-VPS recommendation (recorded as a rejected alternative below).
- Q: How is `ops-agent` handled in devclaw's compose fragment for this slice? → A: **Referenced by image tag** — devclaw's fragment *runs* `ops-agent` (in the `devclaw` compose project), but its image is still *built* by lifekit-stack (kept there as a build-only service). Moving the `ops-agent` source into the devclaw repo is deferred to a later slice.
- Q: What lands in this slice? → A: **Both repos.** The devclaw-repo artifacts (from-source Dockerfile, compose fragment, scoped deploy script, dispatch-only CI) AND the lifekit-stack companion (strip the devclaw Dockerfiles/build stanzas + md5-verify, reference the devclaw image by tag). The live VPS cutover is operator-run, guided by the runbook.

## Context & Problem

Today the devclaw runtime (`devclaw-mcp` + `devclaw-sandbox` + the `devclaw-state` volume + `ops-agent`) is deployed as part of a single **lifekit-stack** monolith:

- **One `docker-compose.yml`** describes devclaw services alongside `openclaw-gateway`, `openclaw-cli`, `lifekit-dashboard`, `notify-relay`, `google-workspace-mcp`.
- **One 388-line `scripts/deploy.sh`** rebuilds the WHOLE stack on every deploy (gateway, dashboard, skills npm install, session reset, health checks) — there is no way to deploy just devclaw.
- **One `ci.yml` deploy job auto-fires on every push to main**, running that whole-stack `deploy.sh`. A routine PR merge therefore triggers a full-stack rebuild that SIGKILLs any in-flight goal sandboxes.
- The devclaw image Dockerfiles **live in lifekit-stack** and `git clone` the devclaw source from GitHub at image-build time against the moving `main` ref. Because BuildKit can't tell a moving ref advanced, `deploy.sh` forces `--no-cache` on both images every deploy (~6-8 min) and adds an **md5-verify-against-GitHub** stage purely to catch the clone-inside-build shipping stale code.

This violates devclaw's own doctrine — "each project owns its deployment" and "deploys are an operator decision, not automatic" (`weekly-release.yml` explicitly states this, yet `ci.yml` does the opposite). The runtime is already **loosely coupled** (the waiter agent reaches devclaw over the network at `http://devclaw-mcp:8000/mcp`; everything else shared is host-level: `~/.claude` OAuth, `docker.sock`, `tailscaled.sock`, the `/srv/memory` vault, gh/git config). What is monolithic is the **deployment orchestration**, not the architecture — so this is a packaging/boundary change, not a runtime rewrite.

## User Scenarios & Testing *(mandatory)*

The "user" here is the **operator** (Denys) and the CI system that deploys on their behalf.

### User Story 1 - Deploy devclaw without touching the OpenClaw stack (Priority: P1)

The operator ships a new devclaw version to the VPS. Only the devclaw services (`devclaw-mcp`, the `devclaw-sandbox` image, `ops-agent`) rebuild and recreate. `openclaw-gateway`, `openclaw-cli`, `lifekit-dashboard`, `notify-relay` are **untouched** — no gateway restart, no session reset, no dashboard rebuild.

**Why this priority**: This is the whole point — a clear boundary. Without it, nothing else matters.

**Independent Test**: Run the devclaw-only deploy path; confirm (via `docker ps` / container IDs / uptime) that the OpenClaw-side containers were not recreated, while `devclaw-mcp` picked up the new image, and the waiter agent can still reach devclaw over the network afterward.

**Acceptance Scenarios**:

1. **Given** a running full stack, **When** the operator deploys a new devclaw version, **Then** only devclaw containers are recreated and OpenClaw-side container uptimes are unbroken.
2. **Given** the devclaw deploy just completed, **When** the waiter agent sends an MCP call to `http://devclaw-mcp:8000/mcp`, **Then** it succeeds against the new version.
3. **Given** the new devclaw is live, **When** the operator inspects `/health` or `/node.json`, **Then** it reports the new commit SHA / build timestamp.

### User Story 2 - devclaw's source, image build, and deploy config live in the devclaw repo (Priority: P1)

devclaw carries everything needed to produce its own deployable image(s): a Dockerfile that builds the MCP-server image **from the checked-out source** (no `git clone` inside the build), the sandbox image build, a compose fragment describing the devclaw services + the `devclaw-state` volume + the network seam, and a deploy script scoped to devclaw only. lifekit-stack no longer builds devclaw — it references the pre-built image tag.

**Why this priority**: Co-locating source with its build/deploy is what makes independent deployment real and kills the clone-inside-build workarounds at the root.

**Independent Test**: From a fresh checkout of the devclaw repo alone, the image can be built and the deploy script located and read — nothing about producing the devclaw image requires the lifekit-stack repo. lifekit-stack's compose references devclaw's image by tag and contains no devclaw Dockerfile or build stanza.

**Acceptance Scenarios**:

1. **Given** the devclaw repo checked out at a commit, **When** the image is built, **Then** it contains exactly that commit's source (verified by the built-in SHA on `/health`), with no network clone step in the build.
2. **Given** the image build, **When** the same source is rebuilt without changes, **Then** BuildKit reuses cached layers (no forced `--no-cache`).
3. **Given** lifekit-stack, **When** its compose and deploy are inspected, **Then** they contain no devclaw Dockerfile, no `git clone` of devclaw, and no md5-verify-against-GitHub stage.

### User Story 3 - A merge to main no longer auto-deploys the VPS (Priority: P2)

Merging a PR to devclaw's main branch runs tests/lint but does **not** automatically rebuild-and-recreate the live VPS instance mid-run. Deploy is operator-triggered (`workflow_dispatch` button and/or a scoped deploy script the operator runs), so a routine merge never SIGKILLs an in-flight overnight goal.

**Why this priority**: Removes the doctrine violation and the live-run disruption. Depends on US1/US2 existing but is a distinct behavior change.

**Independent Test**: Merge a no-op PR to main; confirm no deploy job ran against the VPS and no devclaw container was recreated.

**Acceptance Scenarios**:

1. **Given** a green PR, **When** it merges to main, **Then** no VPS redeploy is triggered automatically.
2. **Given** the operator wants the new version live, **When** they trigger the deploy (dispatch/script), **Then** devclaw redeploys.

### Edge Cases

- **State must survive.** A devclaw redeploy MUST preserve the `devclaw-state` volume (`devclaw.db` + goals). State loss is a regression, not a goal. If the two-compose-project split changes the volume's project namespace, the volume must be declared `external` so it keeps its identity across the cutover.
- **In-flight work on redeploy.** Recreating `devclaw-mcp` still SIGKILLs in-flight sandboxes and restarts the heartbeats; crash-recovery (`queue.recover()`) must still reap and resume them — this behavior is unchanged and must not regress. (Operator-gating the deploy is what keeps this from happening on random merges.)
- **OAuth-only preserved.** The new image build and runtime MUST keep the OAuth-only invariant: no `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` baked or leaked, `~/.claude` still mounted from the host (Constitution I).
- **Network seam intact.** The waiter agent in `openclaw-gateway` must still resolve and reach `devclaw-mcp` after the split (shared/external network or documented address).
- **Sandbox image freshness.** The per-task `devclaw-sandbox` image the MCP server spawns must match the running MCP version (same source), without the md5-verify crutch — achieved by both building from the same checked-out source at deploy time.
- **First-deploy / cold VPS.** The devclaw deploy path must be runnable on a host where devclaw isn't yet running (create the volume, network, containers) as well as idempotently re-runnable.
- **Rollback.** Because images are tagged by SHA, the operator can redeploy a prior SHA tag to roll back without a source revert.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The devclaw repo MUST contain a Dockerfile that builds the `devclaw-mcp` server image from the checked-out working tree (COPY-from-source / local `pip install`), with **no `git clone` of devclaw inside the build**.
- **FR-002**: The devclaw repo MUST own the `devclaw-sandbox` image build (from its existing `.sandcastle/Dockerfile`) so the spawned per-task image is built from the same source as the MCP server.
- **FR-003**: The devclaw repo MUST contain a compose fragment describing the devclaw services (`devclaw-mcp`, `ops-agent`), the `devclaw-state` volume, and the network/host seams needed to run — sufficient to bring devclaw up independently of the OpenClaw stack.
- **FR-004**: The devclaw repo MUST contain a deploy script/path scoped to **devclaw only** — it MUST NOT rebuild or recreate OpenClaw-side services (gateway, cli, dashboard, notify-relay).
- **FR-005**: Building the devclaw image MUST be a normal cache-respecting build — no forced `--no-cache` on every deploy — and MUST NOT require an md5-verify-against-GitHub stage.
- **FR-006**: The built image MUST be tagged by commit SHA (and a moving `latest`/`main` convenience tag), and the running instance MUST surface its SHA (e.g. on `/health` / `/node.json`).
- **FR-007**: lifekit-stack MUST reference devclaw's pre-built image by tag and MUST NOT contain a devclaw Dockerfile, a devclaw `git clone`, a devclaw `build:` stanza, or the md5-verify stage after this change.
- **FR-008**: A merge to devclaw's main branch MUST NOT automatically rebuild-and-recreate the live VPS instance; deploy MUST be operator-triggered.
- **FR-009**: A devclaw redeploy MUST preserve the `devclaw-state` volume (`devclaw.db` + goals) — no state loss across the cutover or on any subsequent deploy.
- **FR-010**: The runtime MUST preserve the OAuth-only invariant (no API key baked/leaked; `~/.claude` mounted from host) and the existing host seams (`docker.sock`, `tailscaled.sock`, `/srv/memory` vault, gh/git config).
- **FR-011**: After the split, the waiter agent MUST still reach `devclaw-mcp` over the network, and `ops-agent` MUST still read `devclaw-state` read-only.
- **FR-012**: The devclaw deploy path MUST be idempotent and runnable both for an in-place upgrade and a cold first-deploy (create volume/network/containers if absent).
- **FR-013**: The change MUST be cut over without a durable-state-losing outage — the migration order MUST keep the `devclaw-state` volume intact when moving devclaw from the shared compose project to its own.
- **FR-014**: Deploy MUST verify readiness after recreate (devclaw-mcp answers `/health`) and fail loudly (non-zero, actionable message) if the new container doesn't come up — matching the loud-failure doctrine.

### Key Entities

- **devclaw image (`devclaw-mcp`)**: the MCP-server runtime image, built from source, tagged by SHA, run on the VPS.
- **devclaw-sandbox image**: the per-task ephemeral image the MCP server spawns via the host docker socket; built from the same source.
- **devclaw compose fragment**: the service/volume/network declaration that brings devclaw up as its own unit.
- **devclaw deploy path**: CI job (`workflow_dispatch`) and/or operator script that builds + recreates devclaw only.
- **devclaw-state volume**: durable SQLite + goals state; must persist across recreates and the cutover (likely declared `external`).
- **The seam**: the network address (`devclaw-mcp:8000`) + shared host resources through which OpenClaw and devclaw stay integrated at runtime while being deployed independently.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Deploying a new devclaw version recreates **zero** OpenClaw-side containers (gateway/cli/dashboard/notify-relay uptimes unbroken).
- **SC-002**: A devclaw deploy that only changes devclaw source completes without a forced `--no-cache` full rebuild — an unchanged rebuild is a near-instant cache hit; a source change rebuilds only the affected layers.
- **SC-003**: Producing the devclaw image requires only the devclaw repo — the lifekit-stack repo is not needed to build it.
- **SC-004**: Merging a PR to devclaw main triggers **no** automatic VPS redeploy (verified: no deploy job run, no container recreate).
- **SC-005**: Across the cutover and every subsequent deploy, all pre-existing durable goals and the run schedule remain present (zero state loss).
- **SC-006**: lifekit-stack no longer contains any devclaw Dockerfile, `git clone` of devclaw, or md5-verify stage.
- **SC-007**: The whole devclaw deploy (build + recreate + readiness) finishes materially faster than the current whole-stack `deploy.sh` on a devclaw-only change, and the operator can roll back to a prior SHA tag without a source revert.

## Assumptions

These are the informed defaults chosen where the description left latitude; the ones marked to confirm are settled in `/speckit-clarify`.

- **Build location — RESOLVED (clarify 2026-08-15): registry**. The image is built from the checked-out source on the self-hosted `lifekit-vps` runner and **pushed to `ghcr.io/lifekit-hq/devclaw-mcp`** (and `…/devclaw-sandbox`), tagged `<sha>` + `latest`; the deploy step `docker pull`s the tag and recreates. This decouples the build host from the run host and gives an off-box image archive (rollback = pull a prior SHA tag), at the cost of GHCR auth on the runner + a pull step. *Rejected alternative — local-build-on-VPS (no registry)*: simpler (builder == run host, no auth/pull) and was the spec's first recommendation, but keeps images only on the box (no off-box archive) and couples build to that one host; rejected in favor of the registry's portability and rollback story.
- **Console bundle**: The Vite+React console under `devclaw/server/console` (currently built in the lifekit-stack Dockerfile) moves into devclaw's own Dockerfile build so the image is self-contained. Assumed in scope.
- **Sandbox image ownership**: The `devclaw-sandbox` image moves to devclaw's ownership (it's already built from devclaw's `.sandcastle/Dockerfile`). Assumed in scope.
- **Two compose projects, shared seams**: devclaw runs as its own `docker compose -p devclaw` project; the `devclaw-state` volume and the OpenClaw↔devclaw network are declared **external** so both projects share them without one owning the other. lifekit-stack keeps the OpenClaw project.
- **Host resources stay host-level**: `~/.claude` OAuth, `docker.sock`, `tailscaled.sock`, `/srv/memory`, gh/git config are bind-mounted from the host in both cases — the split doesn't change them.
- **`ops-agent` moves with devclaw** as its watchdog (reads `devclaw-state` read-only).
- **Cutover**: The `devclaw-state` volume is preserved by adopting it as an external volume (same name/data) before the first devclaw-only deploy, so no goals are lost. Exact migration order to be detailed in the plan.
- **This is a cross-repo change**: primary work + this spec live in the devclaw repo; a companion change lands in lifekit-stack (remove build, reference image tag, split deploy). No devclaw runtime *behavior* (cognition, gates, state model) changes — this is packaging/deploy only, so it requires **no constitution amendment** (it actively realigns with the "each project owns its deployment" doctrine).
