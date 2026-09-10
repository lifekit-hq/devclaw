# Feature Specification: One credential registry — register once, visible at every hop

**Feature Branch**: `feat/one-credential-registry`

**Created**: 2026-09-08

**Status**: SHIPPED — US1 implemented 2026-09-08; US2 implemented 2026-09-10, RESHAPED the same day on the owner's challenge (below).

**Input**: Denys, 2026-09-08 — "today it was NODE_AUTH_TOKEN, before it was the GitHub token; they are all one thing, credentials. Fix the class once and for all: I want to register it once and have it visible everywhere — and least access needed, only what is needed."

## North-star case *(mandatory — constitution 2.9.0)*

- **Failure moved**: stopped when it shouldn't — a goal held on `mechanical:env` for a credential the host already carries.
- **Number that shows it**: `list_problems` rows of kind `mechanical:env` / `env_deficiency` naming a *registered* credential — 21 terminal in the 14 days to 2026-09-08 (three goals parked on it at the time of writing: fs-557, issue-493, scanner-broad-universe; machine issues #870/#873/#874); loop-health `mechanical:env` idle seconds.
- **Cut when**: a worker reports a registered credential absent while doctor reports it present, after this lands — the registry did not close the hop set and the class is elsewhere; or, the day a third credential is added and its author has to touch more than the registry entry and the deploy secret.

## Root cause *(root-cause skill, 2026-09-08)*

A credential crosses eight hops: Actions secret → `deploy/deploy-devclaw.sh` → `/srv/devclaw/secrets.env` → compose `env_file` → the devclaw container → `docker run -e` (`engine/sandcastle.py`) → the runner's agent allowlist (`runner/runner.py:1614`) → the agent's shell → the tool. Every hop was a hand-written list with its own spelling of the name. Each credential was added to some hops and not others; the miss surfaced at the last hop as a worker's prose, and the host-side probe kept saying OK because it measured a different hop.

Evidence that it is a class, not an instance: the setup-token hit exactly this hop on 2026-08-24 (#644 — in the container, never in the agent); the registry token hit it on 2026-09-08 (verified live: present in `devclaw-devclaw-mcp-1`, 40 chars; the sandbox image passes `-e` through; `runner.py:1614` builds the agent env as an allowlist without it; the verify gate at `runner.py:1243` inherits the container env and so *did* have it). Six instance fixes stacked on the root: tinyspec `registry-token-validity` (shape + probe), `durable-container-secrets` (boot guard), `undeclared-registry-nested-npmrc` (#819), `red-ci-log-to-worker` (#867 — the right move: the host does the privileged read), machine issues #870/#873/#874, two owner resumes.

**Smell**: *the same fact declared N times* — N hops, N allowlists, N spellings, no registry.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Register once, every hop iterates the registry (Priority: P1) — IMPLEMENTED

An operator adds a credential by writing ONE entry (name, purpose, least-privilege scope, `required`/`sandbox`/`agent`, accepted prefixes) and the deploy secret. The boot guard, doctor, the sandbox launcher's `-e` forwarding, the host engine and the runner's agent allowlist all derive their behaviour from that entry. The runner never spells a credential: the host hands it the `agent` names in the task payload (`agent_env`), so the swap-seam contract of spec 011 (stdlib-only, imports nothing) holds.

**Why this priority**: it closes the hop that parked three goals today and the hop that took the instance down on 2026-08-24, with one mechanism instead of a seventh instance fix.

**Independent Test**: set a registered `sandbox`+`agent` credential on the host; the docker argv carries `-e VAR=`, the payload carries `agent_env: [VAR, …]` and no value, and the runner's `_agent_env_vars` returns exactly those names. Spell a registered name anywhere in `devclaw/` outside the registry: the build fails.

**Acceptance Scenarios**:

1. **Given** the registry marks `NODE_AUTH_TOKEN` `agent`, **When** a task is dispatched, **Then** the agent's `npm ci` resolves `npm.pkg.github.com` (the worker no longer reports the credential absent while doctor reports it accepted).
2. **Given** a new credential entry with `sandbox=True, agent=False`, **When** a task runs, **Then** the container env carries it and the agent's shells do not.
3. **Given** a credential name typed as a string key in any `devclaw/*.py` other than `credentials.py`, **When** the suite runs, **Then** `test_no_credential_name_is_spelled_outside_the_registry` fails naming the file.
4. **Given** a pre-042 host that sends no `agent_env`, **When** the runner starts, **Then** it forwards the setup-token alone (the #644 contract) — a mismatched deploy never regresses auth.

### User Story 2 - The hop is verified where the worker runs (Priority: P2)

The runner emits one `AgentEnv` event at session start naming (never valuing) the registered credentials present in the agent's environment — and **refuses to start the agent** when a credential the HOST declared should cross is not there. That refusal is the existing `blocked` / `env` result, so no new plumbing: the task fails closed having spent zero tokens. The resulting hold is keyed on the CREDENTIAL, not on the sentence, and it heals itself the next time any session reports that credential present. A worker's later claim that a credential is missing, when the runner saw it arrive, is false and brakes nothing in either mode.

**Why this priority**: US1 removes today's cause; US2 stops the next one costing a session and an owner verb.

**Reshaped 2026-09-10, before merge (Denys).** The first implementation kept the stop and made its *wording* better: a report naming a present credential was filed as "present but unusable", and under `strict` it raised a Problem. Denys rejected it as a workaround, and he was right on all three counts:

- *"It's like having the container and not mounting the credentials. I don't need to spin up an agent session to understand that."* A missing mount is mechanical. Spending a full Claude session to learn `test -n "$VAR"` is the waste, and no amount of better wording after the fact recovers it.
- A Problem is still an owner verb. Making the owner's question nicer is not removing it.
- The live catalog on 2026-09-10 showed one missing `NODE_AUTH_TOKEN` occupying **three separate rows**, because the row id was a slug of the worker's free prose and the worker rephrased it every session. Prose has no identity, so the brake had no release condition, so a human had to be the release condition.

What replaced it, in the order the failure happens:

1. **Refuse before the session.** The host assembles the agent env from its own registry list. Comparing that list against reality costs microseconds and no project knowledge — the host declared it, so its absence is a fact about devclaw, never about this repo.
2. **One credential, one row.** `credential_cap_id(var)` keys the hold on the variable, so every wording collapses to the same row.
3. **It heals mechanically.** `read_result` reads a credential row GREEN once any session reports that credential present. Fix the mount, redeploy, and the next run clears it — no `resume_goal`, no vouch. A brake that cannot observe its own release is the defect, which is exactly why the human-only exit existed.
4. **A false claim brakes nothing.** In both modes, with a log line, no row and no Problem.

What is deliberately unchanged: a gap naming no registered credential — a missing tool, no Docker daemon, the wrong architecture — keeps today's row and today's human exit. The worker is the only witness there and devclaw can probe nothing. That population is the honest remainder of spec 032 US4, which was cut on 2026-09-10.

### Edge Cases

- A blank value at any hop is absence (the boot guard's rule), so `sandbox_env` forwards nothing for it and the agent never sees an empty variable.
- The refused metered keys are part of the registry (`REFUSED`) and are stripped by ONE function at every host subprocess; the runner's own refusal stays as belt-and-braces and is allowed to spell them (it cannot import the registry).
- `agent` without `sandbox` cannot exist; the registry test asserts it.
- The deploy script (bash) still spells the two names for the secrets file; it is outside the package and outside the guard. Its shape assertion uses the same prefixes as the registry by construction of the value it writes; a drift there is caught by doctor's shape check, which reads the registry.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `devclaw/credentials.py` is the only module in the package that spells a credential's environment-variable name; it is a leaf (import-linter contract).
- **FR-002**: Each entry declares `var`, `purpose`, `scope` (least privilege, non-empty), `required`, `sandbox`, `agent`, `prefixes`.
- **FR-003**: The boot guard's required set, the sandbox launcher's `-e` set, the host engine's and the launcher's refused-key strip, and doctor's shape rule are derived from the registry.
- **FR-004**: The task payload carries `agent_env`: the registry's `agent` names, never values; the runner forwards exactly those from its own environment; absent key ⇒ the pre-042 contract (setup-token only).
- **FR-005**: A structural test fails the build on a registered or refused name used as a string constant outside the registry (docstrings and bare-expression strings excluded).
- **FR-006** (US2): The runner emits `agent_env` (names present) at session start; the host records it and classifies a worker env report that names a present credential as present-but-unusable.

### Key Entities

- **Credential**: name, purpose, scope, required, sandbox, agent, prefixes.
- **REGISTRY**: the ordered tuple every hop iterates. **REFUSED**: the metered keys.

## Success Criteria *(mandatory)*

- **SC-001**: After the redeploy, no `env_deficiency` machine issue names a registered credential for 14 days (baseline: 3 in the 24h to 2026-09-08 14:00 UTC).
- **SC-002**: The three goals held on the registry token today resume on the next capability sweep with no owner `resume_goal`.
- **SC-003**: Adding a third credential is a one-entry diff plus the Actions secret; `git diff --stat` of that PR touches `credentials.py`, the deploy script and docs only.

## Assumptions

- The sandbox image and the host deploy together (the image is tagged with the devclaw sha), so the payload key and the runner's reader ship in the same deploy; the fallback covers the mismatch anyway.
- `NODE_AUTH_TOKEN` stays `read:packages`-only; no GitHub token with any other scope enters the sandbox — the host does privileged reads (#867 pattern).

## Rejected alternatives

- **Forward `NODE_AUTH_TOKEN` in the runner and stop** (the one-line fix): the seventh instance fix on the same root; the next credential repeats #644. Kept as the *effect* of US1, rejected as the *mechanism*.
- **Hardcode the agent set in the runner**: makes the runner spell credentials — the exact drift this spec removes, and a spec 011 violation (the runner is the swap seam and imports nothing).
- **Pass the whole container env to the agent**: least privilege lost; the refused-key posture would rest on one strip instead of an allowlist.
- **A doctor check that spawns a sandbox to probe the agent hop**: heavy, docker in a diagnostic, and it measures a fresh container rather than the session that failed. US2's session-start fact is the same evidence at zero extra cost.

## Clarifications

### Session 2026-09-08

- Q: Class or instance? → A (Denys): class — "register once, visible everywhere, least access needed". Spec, not the tinyspec that was being written.
- Q: Does the runner get the registry? → A: no; it gets the names in the payload (spec 011 holds).
