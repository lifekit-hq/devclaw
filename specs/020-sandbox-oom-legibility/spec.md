# Feature Specification: Sandbox OOM Legibility and Prevention

**Feature Branch**: `020-sandbox-oom-legibility`

**Created**: 2026-08-26

**Status**: Draft

**Input**: User description: "Implement devclaw issue #702 — sandbox OOM legibility + prevention. An OOM-killed sandbox settles as a deterministic environment failure with an actionable reason and no identical auto-retry; the runner shields the supervisor so memory exhaustion kills the workload visibly instead of the agent; the sandbox env carries the real memory/CPU allocation so the worker can bound its tooling (/proc/meminfo and nproc lie inside cgroups); the project registry accepts per-project sandbox sizing alongside ADR 0005's sandbox_image. Operator constraint (ruled 2026-08-26): bounded-memory-first, wall-clock second."

## Context — the incident this generalizes

2026-08-26: goal `lkc-type-rollup-2026-08-26` burned two dispatches (~50 min) on
"The Claude Agent process exited unexpectedly" and dispatch-cap-blocked with
"review the open PRs" (there were none). Root cause: the in-sandbox agent
(~1 GB) and lifekit-common's Angular test run shared one 2 GB cgroup with no
swap; the kernel OOM killer took the agent. The failure was (a) invisible —
classified as a generic retriable task failure, (b) retried identically —
deterministic given the environment, so the retry reproduced it, and
(c) undiagnosable from inside — the worker checked memory, read the HOST's
16 GB from `/proc/meminfo`, ruled memory out, and re-ran the killer command.
This spec fixes the class, not the instance (design doctrine 2026-07-18).
Fix-direction detail lives in issue #702 and its design comment.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - An OOM death is legible and never retried unchanged (Priority: P1)

When a sandbox run dies because the container hit its memory cap, the task
settles as a **deterministic environment failure**: the terminal reason names
the cap that was hit and the remedy (raise the sandbox memory for this
project/instance, or bound the verify workload), and devclaw does NOT
re-dispatch the identical attempt — the same policy as the existing
prompt-too-long class. If the goal has nothing else to try, its block reason
says "environment cap", not "review the open PRs".

**Why this priority**: This is the legibility floor. Every other story reduces
how often OOM happens; this one guarantees that when it happens the operator
learns the truth in one dispatch instead of burning the cap on identical
retries and reading a misleading block message. It also protects quota — the
incident spent ~50 minutes of subscription time reproducing a deterministic
failure.

**Independent Test**: Seed a fake engine result that simulates an
OOM-killed container; assert the task settles failed with a reason naming the
cap, that no second dispatch of the identical prompt occurs, and that the
goal's block reason carries the environment-cap class.

**Acceptance Scenarios**:

1. **Given** a task whose sandbox died with OOM-kill evidence, **When** the
   engine settles it, **Then** the task error names the effective memory cap
   and the two remedies, and the settlement is marked deterministic (no
   in-task agent retry, no identical goal-level re-dispatch).
2. **Given** a goal whose only dispatch failed with the environment-cap class,
   **When** the goal blocks, **Then** the block reason states the environment
   cap and the failing command context, not a PR-review instruction.
3. **Given** a sandbox that died WITHOUT OOM evidence, **When** the engine
   settles it, **Then** classification and retry behavior are unchanged from
   today (no false positives).

---

### User Story 2 - Memory exhaustion kills the workload, not the supervisor (Priority: P2)

When the sandbox approaches its memory cap during a heavy child workload
(build, test suite, browser), the process that dies is the **workload**, and
its death is visible to the in-sandbox agent as a failed command ("Killed" in
tool output) that it can adapt to — smaller batches, capped workers, serial
runs. The agent session itself survives memory exhaustion caused by its
children.

**Why this priority**: This converts every future OOM from a fatal,
task-ending event into a recoverable signal inside the session. In the
incident, attempt 1's agent SAW its test run get killed and could have
adapted — it died only because the second exhaustion took the supervisor.
With this story, US1's terminal path becomes the rare fallback (the agent
itself ballooning) instead of the common case.

**Independent Test**: In a memory-capped sandbox, spawn a child that
exhausts memory; assert the child is the process killed, the agent turn-loop
continues, and the failed command's output shows the kill.

**Acceptance Scenarios**:

1. **Given** a capped sandbox with the runner and agent active, **When** a
   spawned workload exhausts container memory, **Then** the workload process
   is killed, the agent receives the failure as command output, and the
   session continues.
2. **Given** the shield in place, **When** the run ends normally, **Then**
   workload exit codes, output capture, and the verify gate behave exactly as
   before (the shield is inert on the happy path).

---

### User Story 3 - The worker can see its cage (Priority: P3)

The worker's environment carries the sandbox's REAL resource allocation —
memory cap and CPU allocation — and the worker-facing guidance tells the
agent to bound its tooling from those values (test-runner worker counts, node
heap limits, batch sizes), because the kernel-reported numbers
(`/proc/meminfo`, `nproc`) reflect the host, not the cgroup. Guidance encodes
the operator ruling: bounded-memory-first, wall-clock second — a slower
serial/capped run that stays inside the cap beats a faster parallel run that
risks the OOM killer.

**Why this priority**: Prevention at the source. An agent that knows "cap is
4g and ~1g is me; 2 CPUs" sizes its tooling correctly BEFORE running, and
US2's kills become rare. Also fixes the incident's false diagnosis path
(host-lying `/proc/meminfo`) and the latent `-n auto`/`maxWorkers` overshoot
(worker sizing by host CPU count under a 2-CPU quota).

**Independent Test**: Launch a sandbox with known sizing; assert the worker
environment exposes those exact values, and that the worker-kind guidance
(canonical home: `runner/skills/`) instructs bounding tooling by them.

**Acceptance Scenarios**:

1. **Given** a sandbox launched with a given memory/CPU allocation, **When**
   the worker inspects its environment, **Then** the declared values match
   the container's actual limits.
2. **Given** the worker guidance, **When** a worker plans a build/test run,
   **Then** the guidance directs it to bound worker counts and heap by the
   declared allocation, preferring bounded-memory over wall-clock speed.

---

### User Story 4 - Per-project sandbox sizing (Priority: P4)

A project can declare its own sandbox memory (and CPU) needs in the project
registry — the sibling of ADR 0005's per-project `sandbox_image` override. A
heavy frontend repo declares e.g. 6g; python repos stay on the instance
default. The instance-wide `DEVCLAW_SANDBOX_MEMORY` remains the default for
projects without an override.

**Why this priority**: Right-sizing as configuration ("the environment IS the
instruction") instead of one global knob ratcheting up forever. Lowest
urgency: the instance-wide knob (reachable since #701) already unblocks
today's workloads.

**Independent Test**: Register a project with a sizing override; assert its
tasks' sandboxes launch with the override while another project's tasks get
the instance default.

**Acceptance Scenarios**:

1. **Given** a project with a registry sizing override, **When** a task for
   it launches, **Then** the sandbox runs with the override, and US3's
   declared values match it.
2. **Given** a project without an override, **When** a task launches, **Then**
   the instance default applies unchanged.
3. **Given** an invalid override value, **When** it is set or used, **Then**
   the failure is loud and actionable — never a silently-ignored value.

---

### Edge Cases

- Workload-kill vs supervisor-kill: the cgroup's OOM counter increments for
  BOTH. A task must be classified as the US1 terminal class only when the
  agent session actually died with OOM evidence; a workload kill the agent
  recovered from (US2) must not fail a task that ends green.
- OOM evidence unavailable at settle (container already reaped, inspection
  fails): classification degrades to today's generic failure — degraded
  legibility must never crash or wedge the settle path, and must never flip a
  failure into a success (fail-closed holds).
- Admission-brake interaction: host-level launch admission sizes by
  `DEVCLAW_SANDBOX_MEMORY + COGNITION_MEM_RESERVE`; per-project overrides
  (US4) must be what admission accounts for, or a large override could
  overcommit the host.
- The agent itself (not a child) balloons past the cap: US2 cannot save the
  session; US1's terminal classification is the backstop and must name the
  cap so the remedy is still legible.
- A cap raised mid-goal (operator fixes env between dispatches): the next
  dispatch must pick up the new sizing without goal surgery.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST determine at task settlement whether the
  sandbox hit its memory cap (OOM evidence from the container/cgroup),
  capturing the evidence before the sandbox is destroyed.
- **FR-002**: A task whose agent session died with OOM evidence MUST settle
  failed with a reason that names the effective memory cap and the remedies
  (raise instance/project sizing, or bound the verify workload), and MUST
  join the deterministic no-auto-retry class (no identical in-task retry, no
  identical goal-level re-dispatch).
- **FR-003**: A goal blocking after an environment-cap failure MUST carry a
  block reason naming that class; the "review the open PRs" phrasing MUST
  only appear when at least one dispatch actually delivered something
  reviewable.
- **FR-004**: Classification MUST be conservative: absent OOM evidence,
  settlement behavior is byte-identical to today; classification failures
  degrade to the generic path and never crash settle or weaken a gate
  (fail-closed preserved; #186 untouched).
- **FR-005**: Workload processes spawned inside the sandbox MUST be
  preferentially selected by the OOM killer over the runner and agent
  processes, and a workload kill MUST surface as ordinary failed-command
  output the agent can read and adapt to.
- **FR-006**: The supervisor shield MUST be inert on the happy path —
  identical exit codes, output capture, and gate behavior when no OOM occurs
  — and MUST NOT require weakening the sandbox's isolation (no new
  privileges/capabilities beyond what the sandbox already has).
- **FR-007**: The sandbox environment MUST declare the container's actual
  memory cap and CPU allocation to the worker, and the values MUST match the
  limits the container was launched with (single source: the launch
  parameters — never re-derived).
- **FR-008**: The canonical worker guidance (one home: `runner/skills/`,
  per the model-agnostic worker-layer invariant) MUST direct workers to bound
  build/test tooling by the declared allocation, encoding
  bounded-memory-first, wall-clock-second.
- **FR-009**: The project registry MUST accept optional per-project sandbox
  memory (and CPU) overrides; task launch MUST apply the project override
  when present, else the instance default; invalid values fail loudly at the
  point of use.
- **FR-010**: Host launch admission MUST account for the effective (possibly
  overridden) sandbox memory of the task being launched, so per-project
  sizing cannot overcommit the host.
- **FR-011**: Per the persisted-state/doctor convention (spec 016 FR-014):
  if the registry schema grows sizing fields, the change ships its doctor
  check; the deployed-instance drift class (documented-but-unreachable knobs,
  the #701 bug) stays pinned by the existing deploy-fragment tests.

### Key Entities

- **OOM evidence**: the post-mortem fact "this container hit its memory cap",
  captured at teardown with the effective cap value; input to classification.
- **Environment-cap failure class**: a terminal task classification meaning
  "deterministic given the environment; retrying unchanged reproduces it";
  sibling of the existing prompt-too-long class.
- **Sandbox sizing**: the (memory, cpus) pair a sandbox launches with —
  resolution order: project registry override → instance default.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An OOM-killed run costs exactly ONE dispatch and settles with a
  reason that names the cap and remedies — versus two dispatches and a
  generic "exited unexpectedly" in the 2026-08-26 incident.
- **SC-002**: A memory-exhausting child workload leaves the agent session
  alive and informed in 100% of seeded-fault runs (the supervisor is never
  the OOM victim while a shielded workload is running).
- **SC-003**: The resource values visible to the worker equal the container's
  launch limits in 100% of launches (zero reliance on host-lying kernel
  interfaces).
- **SC-004**: A project with a sizing override runs its sandboxes at that
  size while other projects are unaffected, verified end-to-end on a live
  dispatch.
- **SC-005**: Zero regressions in the always-hard gate semantics: the change
  adds classification and prevention, never a new path from failure to
  shipped work.

## Assumptions

- Single-host docker deployment (the lifekit-vps shape); cgroup v2 style
  accounting available to the engine for OOM evidence. Exact evidence source
  (container inspect vs cgroup counters) is a plan-level decision.
- The supervisor-shield mechanism is chosen at plan time within FR-006's
  no-new-privileges constraint (candidate: raising each child's own OOM
  score pre-exec, which is unprivileged; the agent's grandchildren inherit
  their parent's score, so where the hook lands is a plan decision).
- Sizing values use docker-accepted unit strings (e.g. "4g"), consistent
  with the existing `DEVCLAW_SANDBOX_MEMORY` knob.
- US1's "no identical re-dispatch" follows the existing prompt-too-long
  precedent, including how failure context reaches any NON-identical future
  dispatch; the misleading "take a strictly smaller slice" advice for this
  class is corrected as part of US1's messaging.
- The stubbed test suite proves classification, messaging, retry policy, and
  sizing resolution; the live behavior of the shield and of real OOM kills is
  proven via the live-shakedown lane, not pytest (suite stays docker-free).
- `DEVCLAW_SANDBOX_CPUS` handling rides along wherever memory does (env
  declaration, registry override) but no CPU-throttling behavior changes.

## Rejected alternatives (direction memory)

- **Docker `--oom-kill-disable`**: trades a legible kill for a hung, paging
  container; worse failure mode, and unsupported on cgroup v2 hosts.
- **`ulimit -v` on children**: virtual-memory limits break JVM/node
  allocators long before RSS matters; blunt and false-positive-prone.
- **A generic `env_file` passthrough for sizing** (during #701): leaks every
  host fact into the container env; explicit substitution keeps the container
  env intentional.
- **Fixing only lifekit-common's test config**: unwedges the instance, not
  the class — the next heavy repo reproduces the whole incident.
- **Raising the instance default cap ever-higher instead of US4**: one global
  knob sized for the heaviest repo overcommits the host for every light repo;
  admission then throttles concurrency for no benefit.
