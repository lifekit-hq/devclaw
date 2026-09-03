# Feature Specification: Environment-capability admission

**Feature Branch**: `030-env-admission`

**Created**: 2026-09-01

**Status**: Implemented (2026-09-02). Generalized by spec 032 (2026-09-03): the capability set gains `ci:definition` (implicit for every registered project) and worker-reported `worker:<item>` rows; the same `mechanical:env` seam holds and heals both.

**Input**: User description: "A project is not dispatchable while a capability its
verify contract depends on is provably broken. Admission consults the mechanical
capability probes, holds dispatches with a `mechanical:env` hold, pings the owner
once, auto-resumes when the probe greens."

## Why (the incident class)

fs-479, 2026-08-27 → 2026-09-01: the sandbox's `NODE_AUTH_TOKEN` was
invalid for days. Every dispatched session hit `npm ci` → 401, and every
session improvised differently around it — skipped the frontend gate ("CI
will check it"), committed test files that had never compiled, bypassed the
pre-commit hook with `--no-verify`. The debt detonated only when the token
healed and the full gate ran for the first time (2026-09-01: a TS2345 in a
test file written blind four sessions earlier). Cost of the class: N burned
worker sessions, silently degraded verification, and un-compiled code
merged across increments.

The doctrine this completes: **the environment IS the instruction.** A
workspace in which the verify contract cannot execute is not a workable
environment; dispatching into it burns a session deterministically. The
instance already KNOWS the capability is broken — doctor's
`instance.registry.token` probe returns red, mechanically, zero-LLM. The
missing piece is one wire: admission must consult what doctor already
measures.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Broken capability holds the project (Priority: P1)

The registry token the instance forwards into sandboxes is expired. A
finance-sentry goal (whose verify runs `npm ci` against a private registry)
comes up for dispatch. Instead of launching a sandbox that will burn a
session on a deterministic 401, admission sees the red capability probe and
holds the dispatch: the goal parks with a `mechanical:env` block naming the
capability and the remedy ("registry token rejected by GitHub — rotate
NODE_AUTH_TOKEN and redeploy"), the owner is pinged exactly once, and zero
tokens are spent while held.

**Why this priority**: this is the burned-session class itself; everything
else in the spec is plumbing around this behavior.

**Independent test**: seed a red probe result; tick a dispatch-ready goal
on a project needing that capability; assert no engine dispatch, the
`mechanical:env` block with the capability named, one owner ping,
`FakeClaude.calls == 0`.

### User Story 2 - Auto-resume when the capability heals (Priority: P2)

The owner rotates the token. On the next probe pass the capability greens;
the held project resumes without any human verb — the same
pause-and-resume shape as the quota brake: the block self-heals, the goal
re-enters the lane, work continues.

**Independent test**: from the US1 held state, flip the probe green; assert
the hold clears on the next tick, dispatch proceeds, and no second ping
was sent.

### User Story 3 - The hold is legible everywhere the operator looks (Priority: P3)

While held, `get_goal`/console show the `mechanical:env` block with the
capability and remedy; doctor's finding and the goal's block name the same
probe id, so the operator sees ONE story, not two.

**Independent test**: held goal's status carries the probe id + remedy
text; doctor output for the same probe uses the same id.

## Requirements

- **FR-001**: A capability probe is a mechanical, zero-LLM check with a
  stable id, producing green/red + evidence (doctor's existing check shape;
  `instance.registry.token` is the reference implementation).
- **FR-002**: Dispatch admission consults the persisted probe results
  before launching a worker for a project that depends on the capability; a
  red probe ⇒ no dispatch, `mechanical:env` block carrying probe id +
  remedy.
- **FR-003**: The block self-heals when the probe greens (heal-budget +
  backoff per the existing mechanical-heal machinery, #228–#238); exactly
  one owner ping per hold episode (`pause_notified` pattern).
- **FR-004**: Probe execution is off the zero-token idle path: probes
  refresh at most once per heartbeat sweep, TTL-cached, and only when at
  least one registered project declares the capability; admission and idle
  ticks read persisted rows, they never probe networks. A held project
  re-probes on the same cadence, so auto-resume lands within ~one sweep of
  the fix. *(Clarified 2026-09-01: per-sweep + TTL.)*
- **FR-005**: A project declares its needed capabilities **explicitly** via
  a `capabilities` key in its `devclaw.json` (e.g.
  `"capabilities": ["registry:npm-github"]`). No derivation, no inference —
  the manifest is the contract; a project that declares nothing is held by
  nothing. *(Clarified 2026-09-01: explicit-only — Denys chose the pure
  environment-as-contract form over derive-with-override.)*
- **FR-005a**: Doctor gains an ADVISORY project check flagging a repo that
  visibly depends on a capability it does not declare (e.g. a lockfile
  resolving against a private registry with no `registry:*` declared) —
  advisory only, never a hold; it exists to catch write-and-forget, the
  known cost of explicit-only declaration.
- **FR-006**: v1 capability set: `registry:npm-github` (the fs-479 class —
  doctor's `instance.registry.token` probe is the reference) and
  `sandbox:image` (image present/pullable — a missing image burns sessions
  identically). Claude auth is explicitly OUT: it is owned by the existing
  usage/auth pause brake. *(Clarified 2026-09-01: registry + image.)*
- **FR-007**: A probe that cannot RUN (infra uncertainty) is `unknown` and
  does NOT hold dispatch — fail-open on uncertainty, fail-closed on
  evidence of breakage (the remote-checks precedent). A red probe is
  evidence; an unrunnable probe is not.
- **FR-008**: Interaction with the runnable-head rule (#786): a
  `mechanical:env`-held goal is not a lane candidate (existing blocked
  skip-over covers it); when the hold clears it becomes runnable again.

## Success Criteria

- **SC-001**: With a red registry probe seeded, a full heartbeat sweep over
  a dependent project dispatches zero workers and spends zero LLM calls.
- **SC-002**: The fs-479 401 arc becomes impossible to reproduce: a broken
  registry token yields exactly one owner ping and zero worker sessions
  until rotated.
- **SC-003**: A project with no declared/derived capability dependencies
  behaves byte-identically to today (no new admission friction).

## Edge cases

- Probe flaps (green↔red): the hold/heal damping rides the existing heal
  budget + backoff; a flapping probe converges to held + one ping, never a
  ping storm.
- Capability breaks MID-session: out of scope — the session fails loud as
  today; admission prevents the NEXT burn.
- Multiple red capabilities: the block names all of them in one message.

## Rejected alternatives

- **Teach workers to handle broken environments** (skill prose): rejected —
  the fs-479 arc shows each session improvises differently around the same
  wall; prose cannot make a deterministic environment failure
  non-deterministic. Fourth-lane doctrine: defaults→environment, not
  instructions (2026-08-25 ownership ruling).
- **Verify-gate-side enforcement only** (fail the settle when a verify step
  cannot execute): rejected as the primary fix — it still burns the session
  to discover what the instance already knew at admission time. May still
  be worth having as a backstop; explicitly out of scope here.
- **Steering/babysitting as the recovery path**: rejected on doctrine
  (owner ruling 2026-09-01): steering is a direction-change verb, never
  required to unstick correctness.

## Clarifications (2026-09-01, walked with Denys one at a time)

- **Q1 — capability declaration**: explicit `capabilities:` key in
  `devclaw.json`, nothing derived. Rejected: marker-derivation (+override)
  and instance-wide holds — Denys chose the pure contract form; FR-005a's
  doctor advisory covers the write-and-forget cost.
- **Q2 — probe refresh**: once per heartbeat sweep, TTL-cached, only when a
  registered project declares the capability. Rejected: doctor-run-only
  (auto-resume stops being automatic) and probe-at-dispatch (network on the
  dispatch path).
- **Q3 — v1 set**: `registry:npm-github` + `sandbox:image`. Rejected:
  docker-daemon probe (a dead daemon already fails loudly today);
  claude-auth (owned by the existing usage/auth pause brake).

## Accepted deviation from FR-004 (owner ruling 2026-09-03)

FR-004 says probes refresh "only when at least one **registered project**
declares the capability". The shipped sweep is deliberately WIDER: after the
registry map, it also scans non-terminal live goals' prepared workspaces, for
goals whose project the registry could not answer for — an unregistered
project, an absent checkout, or a repo with no `devclaw.json`. A capability
declared only by such a goal is therefore probed even though no *registered*
project declares it.

That widening is what makes the brake cover an ad-hoc goal pointed straight at
a checkout; narrowing it to the registry alone would fail those goals OPEN,
which is the SC-002 hole T020/T023 closed from the other side. The cost is
bounded — the scan is skipped for terminal goals, the probes stay TTL-cached
and once-per-sweep, and the per-goal tick path still never probes.

**Denys ruled 2026-09-03: accept the gap and close.** Do not re-litigate it;
if the cost ever shows up, the fix is to require registration, not to remove
the fallback.
