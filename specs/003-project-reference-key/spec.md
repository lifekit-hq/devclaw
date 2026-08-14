# Feature Specification: Registry as the single source of truth for dispatch (project reference key)

**Feature Branch**: `feat/project-reference-key`

**Created**: 2026-08-14

**Status**: Draft — awaiting `/speckit-clarify` (4 open decisions below)

**Tracking issue**: lifekit-hq/devclaw#520 · **Direction memory**: `docs/proposals/project-reference-key.md` (frozen DRAFT #504)

**Input**: Register a project once; reference it by a stable key at dispatch; resolve its workspace, repo, and override knobs server-side from the registry row — so a stale or invented path can never silently reach the engine and fail (or deliver to the wrong repo).

## Why this exists (the class, not the instance)

The registry is **not on the dispatch path**. Every dispatch entrypoint
(`dispatch_task` / `implement_feature` / `fix_bug` / `create_goal`) takes a raw
`workspace_dir` string checked only for non-emptiness; the caller (OpenClaw
waiter, or a human via MCP) can pass any path, and a wrong one is accepted and
fails deep in the engine at sandbox launch — or, worse, *succeeds* and delivers
a PR to the wrong remote. Registry `repoUrl` / `workspace_dir` are stored with
zero validation and drift out of sync with reality; nothing load-bearing reads
them, so the rot is invisible until a run burns on it.

This is one class with two observed instances:
- **2026-08-12** — all three devclaw self-fix dispatches (#491/#329/#501) died
  instantly on a stale host-side path (`/srv/…` from before the container
  migration) plus a stale `dsdevq/` repoUrl.
- **2026-08-14** — a finance-sentry dispatch had to be hand-held because the
  registry pointed `repoUrl` at `dsdevq/finance-sentry` while the canonical
  backlog + review home is `lifekit-hq/finance-sentry`.

Per Constitution VII (fix the class), the rule to change is *where dispatch gets
its facts* — not either individual row.

## User Scenarios & Testing *(mandatory)*

The "users" of this surface are the **OpenClaw waiter agent** and the **human
operator** (Claude on PC via the devclaw MCP) who issue dispatch calls, plus
**devclaw itself** on the self-fix path.

### User Story 1 — Dispatch by project key, resolved server-side (Priority: P1)

A caller dispatches work by naming a registered project, not by typing a path.
devclaw looks up that project's row and resolves the workspace directory, repo
URL, and per-project override knobs itself, at the one choke point every
dispatch call already crosses. An unknown key is rejected immediately, in the
tool call, with an actionable message — never accepted and failed later.

**Why this priority**: This is the whole point — it removes the two failure
modes (invented/stale path reaching the engine; delivery to the wrong repo)
by making the trustworthy registry row the source of dispatch facts. It is the
minimum viable slice: shippable and valuable on its own, before any auto-prep.

**Independent Test**: Register a project; dispatch by its key with no path;
confirm the task runs in the registered workspace and (on delivery) targets the
registered repo. Dispatch an unknown key; confirm a synchronous rejection with
no task row created and zero engine/LLM work.

**Acceptance Scenarios**:

1. **Given** a registered project `finance-sentry` whose row holds the correct
   workspace and `lifekit-hq` repo, **When** a caller dispatches by
   `project_id="finance-sentry"` with no `workspace_dir`, **Then** the task
   runs in the resolved workspace and its PR targets the resolved repo.
2. **Given** no project registered under `ghost`, **When** a caller dispatches
   `project_id="ghost"`, **Then** the call is rejected synchronously with a
   clear "unknown project" error, no task is enqueued, and no container or
   `claude` call is made.
3. **Given** a registered project, **When** a caller passes BOTH a `project_id`
   and a raw `workspace_dir`, **Then** the precedence rule (see Open Decision 1)
   is applied deterministically and the outcome is logged.
4. **Given** a project row whose override knobs (automerge, merge_strategy,
   review_gate, …) are set, **When** work is dispatched by key, **Then** those
   knobs govern the task exactly as they do today via the path-keyed lookup —
   no behavior change to gating/merge.

### User Story 2 — Dispatch-time preflight catches a bad target before work starts (Priority: P1)

When a caller dispatches (by key or by raw path), devclaw verifies at admission
that the resolved workspace exists and is a git repository. A missing or
non-git workspace is handled by the chosen preflight verb (Open Decision 3) —
either a loud synchronous rejection or an auto-prepare — but **never** a claimed
task that dies at sandbox launch.

**Why this priority**: The stale-path incident failed *deep in the engine*
after the task was claimed, wasting the dispatch and the re-dispatch loop.
Moving the check to admission is what makes the failure loud-and-early
(Constitution VI). Pairs with Story 1 to form the P1 slice.

**Independent Test**: Point a registered project at a non-existent workspace;
dispatch; confirm the configured preflight outcome (reject-loud or
prepare-then-run) happens at admission with an actionable reason, and that the
engine/sandbox is never reached on the failing path.

**Acceptance Scenarios**:

1. **Given** a resolved workspace that does not exist on disk, **When** a task
   is dispatched, **Then** preflight triggers the configured verb at admission
   (reject or prepare) — it does not reach `sandcastle` launch as a claimed
   task.
2. **Given** a resolved path that exists but is not a git repository, **When**
   a task is dispatched, **Then** preflight fails it loudly with a reason that
   names the path and the problem.
3. **Given** preflight rejects, **Then** the failure reason is actionable
   (states the path, the cause, and the fix) and no partial task state leaks.

### User Story 3 — Registry rows are trustworthy at write time (Priority: P1)

`register_project` / `update_project` validate and normalize what they store, so
the value read back at dispatch is dependable. A path that is malformed, or a
`repoUrl` that doesn't resolve, is rejected or normalized at write time rather
than stored blindly to rot.

**Why this priority**: Resolution is only as good as the row. The 2026-08-12
row carried a host-perspective path for a container-perspective consumer and
nothing caught it. Write-time validation closes the loop and is small enough to
ride the P1 slice.

**Independent Test**: Attempt to register/update with a malformed path or a
non-resolving repo URL; confirm rejection or normalization with a clear message;
confirm a well-formed write round-trips unchanged.

**Acceptance Scenarios**:

1. **Given** an `update_project` call with a malformed or non-canonical
   workspace path, **When** it is written, **Then** the value is normalized (or
   rejected with a reason) so the stored value is dispatch-dependable.
2. **Given** a `register_project` with a `repoUrl` that does not resolve,
   **When** it is written, **Then** the write surfaces the problem rather than
   storing an unusable URL silently.

### User Story 4 — Missing workspace on a registered project auto-prepares (Priority: P2)

When a registered project's workspace is absent at dispatch, devclaw prepares it
from the row's `repoUrl` (reusing the goal-path clone + `mechanical:prep`
self-heal semantics with damped backoff) instead of failing — extending to the
direct-dispatch path the auto-prep that today exists only on the goal path.

**Why this priority**: Removes the last manual step (the operator SSHing to
clone/fix a workspace) and makes the direct path as self-healing as the goal
path. Deferred to P2 because P1 (reject-loud) is already correct and shippable;
auto-prep is the convenience layer on top.

**Independent Test**: Register a project with a valid `repoUrl` and no
workspace; dispatch; confirm the workspace is cloned/prepared and the task then
runs — with zero LLM cost for the prep step and backoff on repeated failure.

**Acceptance Scenarios**:

1. **Given** a registered project with a resolvable `repoUrl` and no workspace
   on disk, **When** a task is dispatched, **Then** the workspace is prepared
   from `repoUrl` and the task proceeds, at zero `claude` cost for prep.
2. **Given** prep fails repeatedly, **Then** it blocks legibly with backoff (no
   hot loop) and an owner-actionable reason.

### User Story 5 — Internal joins keyed by project id (Priority: P3, named-unsized)

Migrate devclaw's internal workspace-path joins (`find_by_workspace_dir`,
rollups, override resolution) to key on `project_id`, so the workspace path
becomes a resolved detail rather than a join key. Left named-unsized until P1
lands.

**Why this priority**: Purely internal robustness; the P1 "resolution populates,
not replaces" rule keeps all path-keyed joins working, so this is deferrable
with no user-visible gap.

**Independent Test**: With id-keyed joins in place, rename/relocate a workspace
and confirm rollups/override resolution still bind to the right project.

### Edge Cases

- Caller passes both `project_id` and raw `workspace_dir` → precedence per Open
  Decision 1, logged.
- Two projects registered against the same workspace path (legacy) → resolution
  must be deterministic and not silently pick one.
- `project_id` resolves but its `repoUrl`/knobs are `null` → fall back to
  devclaw-wide env defaults exactly as today; absence is "unknown", never a
  guess.
- Container vs host path perspective (Open Decision 4) — the stored path must be
  meaningful to the process that resolves it.
- A `review_repository` (read-only) dispatch by key resolves the same way and
  reviews the resolved branch/workspace.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The dispatch entrypoints (`dispatch_task`, `implement_feature`,
  `fix_bug`, `review_repository`, `create_goal`) MUST accept a `project_id`
  reference key that resolves `workspace_dir`, `repo_url`, and the per-project
  override knobs from the registry row.
- **FR-002**: Resolution MUST happen server-side at the single dispatch choke
  point every entrypoint crosses (today `server/tools.py:79`), not in each
  caller.
- **FR-003**: An unknown `project_id` MUST be rejected synchronously in the tool
  call with an actionable error; no task/goal row is created and no engine or
  `claude` work occurs (Constitution III + VI).
- **FR-004**: Dispatch MUST preflight the resolved workspace at admission — it
  exists and is a git repo — before the task is claimed; a failing preflight
  MUST NOT reach sandbox launch as a claimed task.
- **FR-005**: A failing preflight MUST fail loud with a reason naming the path,
  the cause, and the remedy (Constitution VI).
- **FR-006**: `register_project` / `update_project` MUST validate and normalize
  the stored `workspace_dir` (and surface a non-resolving `repoUrl`) so the row
  is dispatch-dependable at read time.
- **FR-007**: Resolution MUST *populate* the resolved `workspace_dir` onto the
  task/goal row (not replace the join model), so all existing path-keyed
  internal joins keep working unchanged in P1.
- **FR-008**: Raw `workspace_dir` MUST remain accepted as a deprecated-but-
  working alternative through P1 (migration path per Open Decision 2); its
  behavior is byte-unchanged when passed.
- **FR-009**: Override-knob and status behavior (automerge, merge_strategy,
  review_gate, verify_done, sandbox_image, rollups) MUST be identical whether a
  task was dispatched by key or by raw path.
- **FR-010** *(P2)*: A missing workspace on a registered project MUST auto-
  prepare from `repoUrl` reusing the goal-path clone + `mechanical:prep`
  semantics (damped backoff, zero `claude` cost), per Open Decision 3.
- **FR-011**: No requirement here introduces a tick-path/idle LLM call or an
  API-key path; the preflight and resolution are cheap filesystem/SQLite checks
  ordered before any cognition (Constitution I + III).

### Key Entities

- **Project (registry row)**: the durable record keyed by `project_id`, holding
  `workspace_dir`, `repo_url`, override knobs, status. Becomes the authoritative
  source of dispatch facts.
- **Dispatch request**: a call naming a project (by key, or legacy raw path)
  plus task specifics; resolved into a concrete workspace + repo + knob set
  before admission.
- **Preflight result**: the admission-time verdict (ok / missing / not-a-git-
  repo) that gates whether a task is claimed.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of dispatches that name an unknown project are rejected in
  the tool call with zero task rows and zero `claude`/container invocations
  (asserted by a named regression test).
- **SC-002**: 100% of dispatches whose resolved workspace is missing or non-git
  are caught at admission — zero reach `sandcastle` launch as a claimed task
  (the 2026-08-12 failure mode cannot recur).
- **SC-003**: A dispatch by project key delivers to the repo recorded on the
  row in 100% of cases — a registry `repoUrl` change is sufficient to change the
  delivery target (the 2026-08-14 mismatch cannot recur once the row is right).
- **SC-004**: Override-knob / merge / rollup behavior is unchanged: the full
  existing suite stays green and by-key vs by-path dispatch produce identical
  gating decisions.
- **SC-005**: Zero-token idle guarantee preserved — the resolution/preflight add
  no LLM call on any path; the `FakeClaude.calls == 0` guard tests stay green.

## Assumptions

- The registry (`project_registry`) already stores everything resolution needs
  (`workspace_dir`, `repo_url`, override knobs); this feature reads and
  validates it, it does not add new stored fields in P1.
- Container-side path convention holds: the registry stores paths meaningful to
  the serving process (the 2026-08-12 violation was a bad *write*, addressed by
  FR-006) — pending Open Decision 4.
- The goal-path `prepare_ws` + `mechanical:prep` self-heal is reusable on the
  direct-dispatch path for P2 (no new prep mechanism invented).
- The OpenClaw waiter prompt can be updated to prefer `project_id`; ownership of
  that update is Open Decision 2.
- No invariant change is required: this feature strengthens Constitution IV/VI
  (trustworthy state, loud failure) and touches no other principle. If clarify
  surfaces a needed invariant change, the constitution is amended in the same
  arc.

## Open Decisions (resolve at `/speckit-clarify`, with Denys)

These are the proposal's four mandatory `[OPEN]`s. A proposed default is given
for each; clarify locks them.

1. **Raw `workspace_dir` deprecation path** — grace period (both accepted while
   waiter + tests migrate) vs hard cutover once the waiter prompt is updated;
   and who owns updating the waiter. *Proposed default: graceful — accept both
   through P1, `project_id` preferred, deprecate raw path in a later slice.*
2. **Unregistered / ad-hoc dispatch** — keep a raw-path escape hatch forever
   (self-fix goals, dry-runs, one-off human dispatch) vs require a registry row
   for everything and special-case the harness internals. *Proposed default:
   keep the escape hatch; the harness's own self-fix registers a row like any
   project.*
3. **Preflight-failure verb** — reject the tool call (caller retries after fix)
   vs accept + block `mechanical:prep` + auto-heal when the clone appears; one
   answer for both goal and direct paths or per-path. *Proposed default: P1
   rejects loud on the direct path; P2 adds auto-prep so the direct path gains
   the goal-path block-and-heal semantics — converging both paths.*
4. **Container/host path duality** — store canonical container-side paths only
   (validated inside the serving process) vs grow an explicit host↔container
   mapping. *Proposed default: container-side only, but now *validated* at write
   time (FR-006) so the convention can't be violated silently as it was.*

## Out of Scope

- Changing the internal join model away from workspace paths (that is P3/Story 5,
  named-unsized).
- New registry-stored fields or a schema migration in P1.
- Any change to how the OpenClaw waiter is hosted/deployed (only its prompt
  preference for `project_id` is in scope, pending Open Decision 2).
