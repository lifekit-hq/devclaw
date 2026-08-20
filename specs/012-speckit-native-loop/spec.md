# Feature Specification: Speckit-Native Amputation (cut every pre-speckit mechanism)

**Feature Branch**: `012-speckit-native-loop`

**Created**: 2026-08-20

**Status**: Draft — awaiting `/speckit-clarify`

**Rollback point**: `pre-amputation-v0.3.0` → `8fe277f` (annotated tag, 2026-08-20)

**Input**: User description: "I want more radical amputation. Get rid of all those old practices that we had before having speckit and keep only what is reliable to run with speckit."

## Context & Motivation *(informative)*

### The test this spec applies

> **Does this mechanism exist because there was no speckit?**
> If yes, it goes. If it is a brake, a gate, or the speckit spine itself, it stays.

Spec 008 moved planning into the sandbox; spec 011 replaced the executor. Both
engines were replaced and the machinery around them was patched rather than
re-derived, so devclaw still carries a full pre-speckit vocabulary in parallel
with the speckit one.

### The decisive finding: the program vocabulary is unreachable

The goal layer has exactly **two** `Action` construction sites:

- `goal/tick.py:485` → `tool="implement_feature"`, hardcoded
- `goal/tick_donegate.py:558` → `tool="review_repository"`, hardcoded

Therefore `tool="start_program"` can never be produced. The other side confirms
it: `task_queue.py:59` — `submit_program()` without pre-planned tasks now
**raises**, *"host program planning was removed (spec 008 shrink)… file a goal
and let the worker plan via speckit."* The only caller that still passes
`planned=` is the test suite.

**The program/DAG concept is kept alive exclusively by its own tests**, while
threading through the two largest files in the tree: 136 references in
`task_queue.py` (2,143 loc), 86 in `state_store/core.py`, 62 in
`server/tools.py`, 23 in `server/http.py`, plus `goal/engine.py`,
`task_notify.py`, `goal/models.py`, `goal/reconcile.py`, `state_store/rows.py`.

The same finding shows `fix_bug` is unreachable from the goal loop — it survives
only through the legacy direct-dispatch MCP tools.

### Pre-speckit mechanisms superseded by a speckit artifact

| Mechanism | Existed because | Superseded by |
|---|---|---|
| Trend detection (`trend_signals`, `trend_detector`, `bookmark`) | sessions forgot across runs | `specs/NNN-*/` **is** cross-session memory — versioned, in git, read every session |
| Repo brief (`goal/repo_brief.py`, `project_docs`) | the workspace is `git clean -fdx`-wiped, so nothing survived | `AGENTS.md`, which the worker skill already makes it read first |
| Scope grill (`elicitation.py`, `scope_grill`) | no scoping ritual existed | `/specify` + `/clarify` **are** the scoping ritual |
| Self-issue filing (`goal/self_issue.py`) | recurring failures needed to become tracked work | the intake → readiness pipeline (specs 006/009), which produces today's queue |
| Host `backlog` / `next` / `goal_docs` | the host owned the plan | `tasks.md` unchecked `[US<n>]` |

### Dead spike code

`engine/claude_sdk.py` + `prompts/sdk-*.md` benchmark against OpenHands, which
spec 011 deleted. `quality/eval_judge.py` and `quality/evals.py` have zero
production references and the former's docstring reads "a digest of the
OpenHands events". `engine/project_image.py` has zero production references.
`slice_guard`'s `PLAN.md` half says in its own docstring it was to be "removed
by US4/shrink", and the worker skill forbids `PLAN.md` outright.
`.claude/rules/cognition-prompts.md` scopes itself to six modules, four of which
no longer exist.

### The duplicated doctrine and the leak it caused

`goal/tick.py:_advance_brief()` pushes four paragraphs of speckit instructions
that `runner/skills/_writes-code/05-speckit-memory.md` — baked into the image,
always loaded — already carries in more detail, including a case the brief has
no answer for (a repo with no `.specify/`).

That block leaked into `lifekit-hq/lifekit-dashboard` PR #69, whose title reads
`feat: [Repo notes — observations handed back by previous devclaw runs…` and
whose body is the complete worker prompt. Reproduced against the real code:
`advance_brief.is_advance_brief()` matches with `startswith(MARKER)`, and
`repo_brief.render_brief_prefix()` prepends the repo-notes block *ahead of* the
marker at `tick_dispatch.py:186`, so every human-facing guard fails open.
`tick_dispatch` computes the sanitized `display` form and re-stamps the ref with
it, but `task_queue.py:1333` hands `deliver_change()` the task row's **raw**
text — two channels, one sanitized, one not, and the PR is built from the wrong
one. Cutting `repo_brief` (above) removes the prefix; FR-004/FR-005 remove the
class.

### Owner rulings *(direction memory — do not relitigate)*

| Ruling | Date | Note |
|---|---|---|
| Radical amputation: cut every pre-speckit practice, keep only what runs reliably with speckit | 2026-08-20 | supersedes the earlier "re-derive the loop" framing |
| Direct dispatch migrates to `create_goal(mode=one_shot)`; the legacy task tools are cut | 2026-08-20 | ADR 0003 "one primitive" finally enforced |
| `self_issue.py` is in scope and is cut | 2026-08-20 | superseded by the intake pipeline |
| `deploy/` + its MCP tools are **deferred**, not cut | 2026-08-20 | see Deferred, below |
| The amputation lands as **one PR**, not a staged sequence | 2026-08-20 | tradeoff accepted; see below |

### Rejected alternatives *(direction memory)*

- **Conservative "re-derive the loop" scope** (move the done-gate verdict,
  keep the mechanisms). Rejected 2026-08-20 — it adds structure without removing
  the parallel vocabulary.
- **Keep `done_when` authoritative and validate its clauses.** Rejected on
  Principle VII: a validator fixes the case that hurt and leaves the class.
- **Staged sequencing** (PR1 provably-dead → PR2 leak fix → PR3 superseded →
  PR4 migration), each independently revertable. **Recommended by the assistant
  and rejected by the owner 2026-08-20** in favour of one PR. Accepted tradeoff:
  a regression bisects against a ~6,400-line diff with no intermediate green
  commit. Mitigation is FR-021 — a recorded green baseline at
  `pre-amputation-v0.3.0` — and the tag itself as the rollback point.
- **Strangle pattern** (deprecate, warn, delete next release). Rejected: it
  leaves the tree carrying both vocabularies, which is the state being ended.

### Deferred — explicitly not in this arc

**`devclaw/delivery/deploy.py` (417), `delivery/repo.py` (156), and the
`deploy_project` / `deploy_status` / `stop_deploy` / `list_deploys` /
`create_repo` / `delete_repo` tools.** These are not pre-speckit residue — they
fail this spec's own test. Both registered projects carry live preview URLs, and
`tick_donegate.py:253` already yields to a project that owns its Dockerfile.
Deleting a live integration inside a structural amputation mixes two different
risks. Revisit as its own decision after this arc lands.

**`devclaw/goal/triage.py` (210) + `prompts/self-triage.md` + its tests.** The
self-triage step turns a raw owner ping into a diagnosed problem with a proposed
fix. It fails this spec's test for the same reason `deploy/` does — it exists
because pings were undiagnosed, not because there was no speckit — and it is
live (`_SELF_TRIAGE_ENABLED = True`, wired into `GoalService`). It is also
directly useful in the state the instance is in today: last night's
`needs_human` park is exactly the ping it would have diagnosed.

## Clarifications

### Session 2026-08-20 (owner, at intake)

- **Scope** → radical amputation, not loop re-derivation.
- **Direct dispatch** → migrate to `create_goal(mode=one_shot)` and cut.
- **`deploy/` and `self_issue.py`** → `self_issue` cut; `deploy/` deferred after
  evidence showed it is live-wired.
- **PR shape** → one PR.

### Session 2026-08-20 (`/speckit-clarify`)

- Q: When the amputation deletes the code that reads the program tables,
  repo-brief docs and trend metadata, what should happen to that data in the
  live database? → A: **Code stops reading; schema untouched.** Orphaned tables
  and columns stay as history; a separate follow-up PR drops them once the
  amputated build has run several clean nightly cycles. Rationale: the
  `pre-amputation-v0.3.0` tag rolls back code, not data — a destructive
  migration in this PR would make rollback asymmetric.

- Q: After the legacy dispatch tools become sugar over `create_goal`, should
  those tool *names* stay callable, or be removed from the MCP surface in this
  same PR? → A: **Names stay as thin deprecated sugar; the implementation is
  amputated.** The OpenClaw waiter drives devclaw over MCP and lives outside
  this repo, so removing names it may still call has no in-PR fix. The real win
  — `queue.submit()` losing every caller outside the goal layer — lands fully
  either way. Matches the existing `start_program` precedent (`tools.py:519`).

- Q: Should `goal/triage.py` be cut in this arc, or deferred alongside
  `deploy/`? → A: **Deferred.** It fails this spec's own test — it exists
  because owner pings were undiagnosed, not because there was no speckit — so
  it belongs with `deploy/` in Deferred. It is live (`_SELF_TRIAGE_ENABLED =
  True`, wired into `GoalService` on the ping path). Recorded correction: the
  assistant had written it into the cut inventory without an owner ruling.

**Still open** — two `[NEEDS CLARIFICATION]` markers, both on **US4 (P2)** and
neither blocking P1: FR-017 (who authors the feature spec from `done_when`) and
FR-018 (policy when a worker edits the criteria it is judged against). Assistant
recommendations are recorded inline for a later clarify pass.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The tree carries one vocabulary (Priority: P1)

Every mechanism that exists because there was no speckit is deleted, together
with its tests, prompts, MCP tools, database columns and documentation.

**Why this priority**: This is the spec. The parallel vocabulary is what makes
every other change in devclaw expensive to reason about.

**Independent Test**: Full suite green at or above the recorded baseline; no
production reference to any removed symbol anywhere in the tree; a nightly cycle
runs end-to-end on the amputated build with no new wedge class.

**Acceptance Scenarios**:

1. **Given** the amputated tree, **When** the full suite runs, **Then** it is
   green at or above the baseline recorded at `pre-amputation-v0.3.0`.
2. **Given** the amputated tree, **When** any removed symbol is searched for,
   **Then** it appears in neither production code nor tests nor docs.
3. **Given** a long-lived goal on a speckit repo, **When** a full nightly cycle
   runs on the amputated build, **Then** it settles work and introduces no
   wedge class absent from the pre-amputation baseline.

**Cut inventory — provably dead**:

- The **program/DAG surface end-to-end**: `program_plan.py`; every `program`
  branch in `task_queue.py`, `state_store/core.py`, `state_store/rows.py`,
  `server/tools.py`, `server/http.py`, `goal/engine.py`, `task_notify.py`,
  `goal/models.py`, `goal/reconcile.py`; the `start_program` / `get_program` /
  `list_programs` / `cancel_program` tools; `tests/test_program_plan.py`,
  `test_start_program_alias.py`, `test_state_program.py`,
  `test_cancel_program_guard.py`.
- `engine/claude_sdk.py` + `prompts/sdk-*.md` (4) + `tests/test_claude_sdk_engine.py`.
- `quality/eval_judge.py` + `quality/evals.py` + `quality/prompts/eval-judge.md`
  + `tests/test_eval_judge.py`.
- `engine/project_image.py` + `tests/test_project_image.py`.
- `slice_guard`'s `PLAN.md` half: `count_milestone_flips`,
  `mega_dump_flips_sync`, `_plan_at_ref_sync`.
- `.claude/rules/cognition-prompts.md`.

**Cut inventory — superseded by speckit**:

- `trend_signals.py`, `trend_detector.py`, `bookmark.py`, their tests, the
  `review_trends` tool, and the `DEVCLAW_TREND_*` environment surface.
- `goal/repo_brief.py`, the `project_docs` `repo_brief` kind, the dispatch-time
  prefix at `tick_dispatch.py:173-186`, and `tests/test_repo_brief.py`.
- `elicitation.py`, `prompts/scope-grill.md`, `prompts/scope-grill-contract.md`,
  the `scope_grill` tool, `tests/test_elicitation.py`.
- `goal/self_issue.py` + `tests/test_self_issue.py`.
- `goal/merge.py` + `tests/test_goal_merge.py` (automerge, default OFF, never
  exercised in production).

---

### User Story 2 - One entry point (Priority: P1)

`dispatch_task`, `implement_feature`, `fix_bug`, `review_repository` and
`onboard` become sugar over `create_goal(mode=one_shot)` and are then retired,
enforcing ADR 0003's "one primitive, one dial".

**Why this priority**: It is a precondition for cutting the legacy tools, and it
is how work is actually started today — so it must land with the amputation, not
after it.

**Independent Test**: Start a bug fix the way it is started today; assert a goal
is created, the identical execution path runs, and no `queue.submit()` call
occurs outside the goal layer.

**Acceptance Scenarios**:

1. **Given** a caller invoking the legacy dispatch surface, **When** the call is
   made during the deprecation window, **Then** a one-shot goal is created and
   the caller receives a goal id.
2. **Given** the migrated surface, **When** the tree is searched, **Then**
   `queue.submit()` has no caller outside the goal layer.
3. **Given** a one-shot goal, **When** it completes, **Then** it runs the same
   dispatch, gate and delivery path as a long-lived goal.

**Known consequence, accepted**: a one-shot then carries a done-gate and can
park `needs_human` where a bare task would simply have failed. This is ADR
0003's "ONE identical execution path" and is intended.

---

### User Story 3 - The brief carries payload, never doctrine (Priority: P1)

The dispatch brief stops restating the worker's baked-in skills, and no
worker-input text reaches a human-facing surface.

**Why this priority**: It fixes a **live defect** — PR #69 is open right now
with a leaked title and body — and it is the last consumer of the repo-notes
prefix US1 removes.

**Independent Test**: Dispatch an advance; assert the worker prompt carries the
payload and none of the four speckit instruction paragraphs, and that the
delivered PR title and body carry no brief or repo-notes text.

**Acceptance Scenarios**:

1. **Given** a dispatched advance, **When** the PR is created or refreshed,
   **Then** its title and body contain no dispatch-instruction text.
2. **Given** a goal branch with three or more increments, **When** delivery
   refreshes the PR, **Then** the title derives from the display form, never the
   raw dispatched goal text.
3. **Given** any prefix prepended to the brief, **When** a human-facing renderer
   asks whether the text is an advance brief, **Then** the answer is correct.

---

### User Story 4 - One contract of record (Priority: P2)

The done-gate's verdict moves from the SQLite `done_when` string to the
executing feature's `spec.md` Success Criteria; `done_when` becomes the seed.

**Why this priority**: **Demoted from P1 deliberately.** The two-contracts
fracture is a verified fact of the code, but its measured cost is a single
incident (`lkd-feed-honesty-2026-08-19`, parked on two GitHub-bookkeeping
clauses). `donegate_churn` has never visibly fired — zero occurrences across 89
problem fingerprints and 19 cycle reports. Most of the observed benefit comes
from FR-016's clause filter alone, which needs no verdict move and no
constitutional amendment. The structural argument for moving the verdict stands;
the empirical one does not yet.

**Independent Test**: Run the done-gate against a repo whose feature Success
Criteria are satisfied but whose `done_when` holds an unsatisfiable clause;
assert the goal closes and the clause is surfaced as a follow-up.

**Acceptance Scenarios**:

1. **Given** all Success Criteria satisfied with repo evidence, **When** the
   done-gate runs, **Then** the verdict is `achieved`.
2. **Given** unchecked `[US<n>]` items in `tasks.md`, **When** the done-gate
   runs, **Then** the goal does not close.
3. **Given** a repo with no committed `.specify/`, **When** the done-gate runs,
   **Then** the existing `done_when` contract governs unchanged, fail-closed.

---

### Edge Cases

- **An unknown caller of a removed MCP tool** (the OpenClaw waiter invoking
  something untraced). The single-PR shape gives no deprecation window, so the
  failure surfaces as a tool error at call time. Mitigated by FR-020's inventory
  and by the rollback tag.
- **A repo with no committed `.specify/`.** The pre-existing contract and
  fail-closed behavior are unchanged; `speckit_setup.py` can install the
  scaffold, so this is the exception path.
- **Repo facts previously held in `project_docs`** are lost when it is cut.
  Anything load-bearing must land in the repo's `AGENTS.md` first — FR-011.
- **`tasks.md` complete but verify red.** Verification wins; checkbox state is
  necessary, never sufficient.
- **Trend history in `trends.md`** on the workstation vault is orphaned by the
  cut. It is historical record and is left in place, not deleted.
- **Orphaned database rows after the cut.** Readers disappear while the tables
  remain. Nothing must crash on their presence, no code may re-acquire a
  dependency on them, and the console must not surface an empty program list as
  though the concept still exists.

## Requirements *(mandatory)*

### Functional Requirements

**US1 — one vocabulary**

- **FR-001**: Every symbol in the two cut inventories MUST be removed from
  production code, tests, prompts, MCP tools and docs.
- **FR-001a**: The database schema MUST NOT be altered by this PR. The
  `programs` table, the program-era `tasks` columns (`program_id`,
  `depends_on`, `order_idx`, `milestone`, `plan_key`), `project_docs`, and the
  trend `meta` rows are left in place as orphaned history — the amputated code
  simply stops reading them. Dropping them is a separate follow-up PR, taken
  only after the amputated build has run several clean nightly cycles.
  Rationale: `pre-amputation-v0.3.0` rolls back code, not data; a destructive
  migration would leave a rollback pointing at a schema that no longer matches.
- **FR-002**: `task_queue.py`, `state_store/core.py`, `server/tools.py` and
  `server/http.py` MUST carry no program/DAG branch after the cut.
- **FR-003**: Removal of a mechanism MUST NOT weaken any gate. The verify,
  test-integrity and done gates stay always-hard; every consulted gate stays
  fail-closed.
- **FR-004**: The repo-notes dispatch prefix MUST be removed at its source, not
  worked around at the renderer.
- **FR-005**: No dispatch-time text MUST be capable of reaching a PR title or
  body; delivery MUST receive the human-facing display form through the task
  row's existing `title` seam.

**US2 — one entry point**

- **FR-006**: The legacy dispatch tools (`dispatch_task`, `implement_feature`,
  `fix_bug`, `review_repository`, `onboard`) MUST create a one-shot goal rather
  than submitting a task directly, following the `start_program` sugar
  precedent (`tools.py:519`).
- **FR-006a**: Those tool NAMES MUST remain callable after this PR, as thin
  deprecated aliases. Only the direct-dispatch implementation behind them is
  removed. The OpenClaw waiter calls them and lives outside this repo, so a
  name removed here has no in-PR fix; retiring the names is a follow-up taken
  once the waiter is confirmed migrated.
- **FR-007**: After migration, `queue.submit()` MUST have no caller outside the
  goal layer.
- **FR-008**: A one-shot goal MUST run the identical dispatch, gate and delivery
  path as a long-lived goal.

**US3 — brief carries payload**

- **FR-009**: The advance brief MUST carry only the marker line, objective, done
  contract, failure context and steering. The four speckit instruction
  paragraphs MUST be deleted; the worker skill is their single home.
- **FR-010**: Advance-brief detection MUST NOT depend on the marker being at the
  start of the text.

**Cross-cutting**

- **FR-011**: Before `project_docs` is cut, any load-bearing repo fact it holds
  MUST be migrated into the target repository's `AGENTS.md`.
- **FR-012**: Documentation made stale — `CLAUDE.md`, `docs/architecture.md`,
  `docs/flows/task-execution.md`, `docs/reference/env-vars.md`, `docs/INDEX.md`
  currency tags, `README.md` — MUST be corrected in the same PR.
- **FR-013**: The constitution MUST be reviewed against the amputated tree and
  amended where a principle names a removed mechanism.
- **FR-014**: Zero-token guard tests (`FakeClaude.calls == 0` on idle and
  blocked paths) MUST stay green. A change that breaks one is wrong.
- **FR-015**: The PR MUST ship named regression tests for each behavior change
  (FR-005 and FR-009 at minimum), named after the behavior.

**US4 — one contract of record (P2)**

- **FR-016**: A `done_when` clause that cannot be expressed as repo behavior
  (closing issues, closing superseded PRs, ticket hygiene) MUST be excluded from
  the contract at seeding time and reported to the owner, never silently block
  closure. *This requirement alone captures most of the observed benefit and may
  be pulled forward into P1.*
- **FR-017**: `done_when` MUST seed the feature's Success Criteria.
  [NEEDS CLARIFICATION: does the host write `spec.md` into the pre-dispatch
  working tree (assistant recommendation: yes — `tick_dispatch` already holds
  `goal.workspace_dir`, the worker's own commit carries the file per its skill,
  and the write is idempotent across failed sessions), or does the first worker
  session author it?]
- **FR-018**: [NEEDS CLARIFICATION: policy when a worker edits the Success
  Criteria in the session being judged against them. Assistant recommendation:
  pin the criteria as of dispatch AND surface the diff on the PR — independence
  from pinning, visibility from surfacing, without ceding authority to the
  executor.]
- **FR-019**: Moving the verdict MUST amend Constitution Principle V and the
  matching `CLAUDE.md` statement in the same PR, with a version bump.

**Safety**

- **FR-020**: The PR description MUST carry the complete inventory of removed
  symbols, tools and env vars, so an unknown caller can be diagnosed from the
  diff alone.
- **FR-021**: A full-suite green baseline MUST be recorded at
  `pre-amputation-v0.3.0` before the cut, and the post-cut count compared
  against it.

### Key Entities

- **Pre-speckit mechanism**: anything that exists because sessions had no
  durable, versioned, machine-readable plan. The amputation target.
- **Speckit spine**: `create_goal` → tick → dispatch → sandboxed speckit worker
  → verify gate → delivery → done-gate. Survives untouched.
- **Brake**: `limits`, `test_integrity`, `dispatch_gate`, `admission`,
  `prompt_budget`, `remote_checks`, circuit breaker, quota pause. All survive —
  this arc subtracts mechanism, never verification.
- **Rollback point**: `pre-amputation-v0.3.0` → `8fe277f`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The tree carries exactly one dispatch vocabulary — no production
  path can produce a program, and no MCP tool offers one.
- **SC-002**: Four concepts leave devclaw's INTERNAL vocabulary entirely:
  program/DAG, trend detection, repo brief, scope grill. No table read, dispatch
  path, model or prompt references them. A surviving deprecated tool alias is a
  name kept for an external caller, not a concept still in the system.
- **SC-003**: `devclaw/` drops by at least 2,650 lines and `tests/` by at least
  2,950, with the full suite green and zero failures. (Down from 2,900/3,300 —
  `goal/triage.py` and its 316 test lines moved to Deferred.)
- **SC-004**: The MCP surface drops from 47 tools to at most 42 — the cut set is
  `get_program`, `list_programs`, `cancel_program`, `review_trends`,
  `scope_grill`. No surviving tool exposes the program/DAG or trend vocabulary;
  the five dispatch names and `start_program` survive only as deprecated
  `create_goal` aliases.
- **SC-005**: No delivered pull request contains dispatch-instruction text,
  repo-notes text, or brief boilerplate in its title or body.
- **SC-006**: A full nightly cycle runs on the amputated build and introduces no
  wedge class absent from the pre-amputation baseline.
- **SC-007**: An idle nightly cycle still costs zero `claude` calls.
- **SC-008**: A repository with no speckit scaffold behaves exactly as it does
  today.

## Assumptions

- **Speckit is the design centre; a non-speckit repo is transitional.**
  `speckit_setup.py` adopts or installs `.specify/`.
- **Git is durable state.** The workspace is `git clean -fdx`-wiped per
  dispatch, so the repository is the only surface that survives — which is
  exactly what speckit uses.
- **The worker's baked-in skills are authoritative for doctrine.** Anything the
  host would push about *how* to work belongs in `runner/skills/`.
- **The intake → readiness pipeline (specs 006/009) stays.** It is live and it
  is what creates work today; explicitly out of scope.
- **`deploy/` stays** for this arc, by owner ruling, on evidence that it is
  live-wired to both registered projects.
- **`goal/triage.py` stays** for this arc, by owner ruling, on the same test
  that spared `deploy/`.
- **Orphaned SQLite tables cost nothing at rest.** Leaving them keeps the
  amputation a pure code change that the rollback tag can fully undo, and
  preserves the forensic history of the instance's 18 goals.
- **The arc runs on a branch in a worktree**, per `.claude/rules/git-workflow.md`
  — no commits on `main`.
