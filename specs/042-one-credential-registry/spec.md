# Feature Specification: One credential registry — register once, visible at every hop

**Feature Branch**: `feat/one-credential-registry`

**Created**: 2026-09-08

**Status**: SHIPPED — US1 implemented 2026-09-08; US2 implemented 2026-09-10. US2's own cut condition — "if no worker reports a registered credential absent by 2026-09-22" — resolved toward BUILD on 2026-09-10, when five of the nine owner resumes in four days carried `environment capability check failed — dispatching would burn a session`.

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

The runner emits one `agent_env` event at session start naming (never valuing) the registered credentials present in the agent's environment. The host records it on the task, and a worker's `BLOCKED: env — <text>` that names a registered credential the runner reported present is filed as *present-but-unusable* (value, scope or usage), never as *absent* — the hold message, the machine issue and doctor's remedy say which. Doctor's credential check reports the last such session-start fact next to the host-side probe, so "the host says OK, the worker says absent" becomes one line with both facts instead of two contradicting surfaces.

**Why this priority**: US1 removes today's cause; US2 makes the next one legible in one read. Built 2026-09-10 on its own condition: workers did report registered credentials absent — five of the nine owner resumes in the four days to that date were a human deciding, by hand, whether a credential had failed to arrive or had arrived and been rejected.

**Independent Test**: a fake runner session emitting `agent_env` present=[X]; a worker block naming X; the hold text says "present in the agent env".

**Acceptance Scenarios**:

1. **Given** the runner reported `NODE_AUTH_TOKEN` present, **When** the worker blocks on it, **Then** the machine issue's title says present-but-rejected and the remedy is the registry token's scope/validity, not "provide it".

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
