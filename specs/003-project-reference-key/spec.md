# Feature Specification: Registry as the single source of truth for dispatch (project reference key)

**Feature Branch**: `feat/project-reference-key`

**Created**: 2026-08-14

**Status**: Clarified 2026-08-14 — all 4 open decisions locked; ready for `/speckit-plan`

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

## Clarifications

### Session 2026-08-14

- Q: Should raw `workspace_dir` stay a permanent escape hatch, or should every dispatch be required to name a registered `project_id`? → A: **Registry required for all** — `project_id` is the mandatory dispatch contract; raw `workspace_dir` is removed as a caller-facing parameter. devclaw's own harness-internal dispatches (self-fix, dry-runs) are special-cased in code, not via a public raw-path parameter.
- Q: How should raw `workspace_dir` be removed once `project_id` resolution lands? → A: **Hard cutover in P1** — the P1 arc removes the raw parameter outright and flips every call site, all tests, and the OpenClaw waiter prompt in lockstep. No deprecation window. The waiter-prompt change (OpenClaw side, Denys-owned GitOps) MUST land in the same window as the devclaw tool-signature change, or dispatch breaks on the VPS.
- Q: On a missing/non-git workspace for a registered project, what should preflight do in P1? → A: **Reject loud in P1, auto-prep in P2.** P1 fails the dispatch at admission with an actionable reason; P2 adds the goal-path block-and-heal that auto-clones from the row's `repoUrl`, converging the goal and direct paths on one self-heal.
- Q: How should the registry handle the container-vs-host path perspective that caused the 2026-08-12 failure? → A: **Container-side only, validated at write time.** The registry stores only paths meaningful to the serving process; `register_project`/`update_project` validate the path resolves inside that process, so a host-perspective path can't be written silently. No new stored field, no translation layer.

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
3. **Given** the dispatch tools no longer accept a raw `workspace_dir`
   parameter, **When** a caller attempts to dispatch without a `project_id`,
   **Then** the call is rejected with a message pointing at `project_id`.
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
   is dispatched, **Then** P1 preflight rejects it loudly at admission (P2 will
   auto-prepare from `repoUrl` instead) — it never reaches `sandcastle` launch
   as a claimed task.
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

- Caller dispatches with no `project_id` → rejected pointing at `project_id`
  (raw `workspace_dir` is no longer a caller-facing parameter).
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
  the stored `workspace_dir` — as a canonical **container-side** path that
  resolves inside the serving process (rejecting a host-perspective path like
  the 2026-08-12 `/srv/…` write) — and surface a non-resolving `repoUrl`, so the
  row is dispatch-dependable at read time. No host↔container mapping is stored;
  the single container-side frame is enforced, not assumed.
- **FR-007**: Resolution MUST *populate* the resolved `workspace_dir` onto the
  task/goal row (not replace the join model), so all existing path-keyed
  internal joins keep working unchanged in P1.
- **FR-008**: `project_id` is the mandatory dispatch contract — raw
  `workspace_dir` is removed as a caller-facing parameter **in P1** (hard
  cutover, no deprecation window). Every in-repo call site and test MUST be
  migrated to `project_id` in the same arc, and harness-internal dispatches
  (self-fix, dry-runs) resolve their workspace in code, never via a public
  raw-path parameter.
- **FR-008a**: The OpenClaw waiter prompt MUST be updated to dispatch by
  `project_id` in the same landing window as the tool-signature change; because
  the cutover is hard, a devclaw deploy that lands the new signatures without
  the matching waiter update breaks dispatch on the VPS. This coordination is a
  named release step, not an afterthought.
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
- Container-side path convention holds and is now enforced: the registry stores
  paths meaningful to the serving process; the 2026-08-12 violation was a bad
  *write*, closed by FR-006's write-time validation.
- The goal-path `prepare_ws` + `mechanical:prep` self-heal is reusable on the
  direct-dispatch path for P2 (no new prep mechanism invented).
- The OpenClaw waiter prompt is updated to dispatch by `project_id`; ownership
  is Denys (OpenClaw GitOps via `dsdevq/lifekit-stack`), landed in lockstep with
  the devclaw deploy (FR-008a). Because the cutover is hard, P1 is larger than a
  pure additive change — it includes migrating every in-repo dispatch call site
  and test — which `/speckit-plan` must size accordingly.
- No invariant change is required: this feature strengthens Constitution IV/VI
  (trustworthy state, loud failure) and touches no other principle. If clarify
  surfaces a needed invariant change, the constitution is amended in the same
  arc.

## Open Decisions — LOCKED 2026-08-14 (`/speckit-clarify`, with Denys)

The proposal's four mandatory `[OPEN]`s, all resolved in the clarify session
(recorded in Clarifications above). Kept here as direction memory — the reasons,
not just the answers.

1. **Raw `workspace_dir` deprecation path** — ✅ **RESOLVED 2026-08-14: hard
   cutover in P1.** Raw parameter removed outright; every call site + test +
   the OpenClaw waiter prompt flip in the same arc; no deprecation window. The
   waiter-prompt update is Denys-owned (OpenClaw GitOps) and is a named,
   lockstep release step with the devclaw deploy.
2. **Unregistered / ad-hoc dispatch** — ✅ **RESOLVED 2026-08-14: registry
   required for all.** Every dispatch names a `project_id`; raw `workspace_dir`
   is removed as a caller-facing parameter. The harness's own internal
   dispatches (self-fix, dry-runs) resolve their workspace in code, special-cased
   — not via a public raw-path parameter.
3. **Preflight-failure verb** — ✅ **RESOLVED 2026-08-14: reject loud in P1,
   auto-prep in P2.** P1 rejects a missing/non-git workspace at admission with
   an actionable reason; P2 adds the goal-path block-and-heal (`mechanical:prep`
   auto-clone from `repoUrl`), converging the goal and direct paths on one
   self-heal semantics.
4. **Container/host path duality** — ✅ **RESOLVED 2026-08-14: container-side
   only, validated at write time.** The registry stores only container-side
   paths; write-time validation (FR-006) confirms they resolve in the serving
   process. No host↔container mapping is added — the incident was a validation
   gap, not a missing-mapping gap.

## Out of Scope

- Changing the internal join model away from workspace paths (that is P3/Story 5,
  named-unsized).
- New registry-stored fields or a schema migration in P1.
- Any change to how the OpenClaw waiter is hosted/deployed (only its prompt
  preference for `project_id` is in scope, pending Open Decision 2).
