# Feature Specification: Planning-Strategy Dial (self_contained | github_issues)

**Feature Branch**: `002-planning-strategy-dial`

**Created**: 2026-08-14

**Status**: Draft

**Input**: User description: "Two strategies for devclaw: one self-contained (planning lives in the repo — PLAN.md), one that relies on GitHub issues (milestones, linking). Selectable, so devclaw can orient and work either way."

## Problem Statement

devclaw has two genuinely different relationships with a repository:

- **"devclaw builds this"** — greenfield or experiment repos (ledger, closeloop,
  the scaffold builds). There is no external backlog; the repo *is* the world.
  Today's model fits: the worker maintains **PLAN.md** in the repo as the
  roadmap + progress spine, and the thin-advance loop reads "the smallest
  not-yet-done milestone in PLAN.md."
- **"devclaw works your curated backlog"** — real product repos (finance-sentry,
  lifekit-dashboard) where the owner already curates **GitHub issues +
  milestones**. There, PLAN.md is redundant and invisible; the issues are the
  natural, human-visible, tooled substrate, and a worker-maintained markdown
  plan duplicates (and can drift from) the real backlog.

Today only the first model exists. This feature adds the second as a **selectable
strategy**, not a replacement — matching devclaw's existing dial pattern
(`delivery_strategy` per-action|goal-branch, `strictness` trust|strict,
`browser_gate_mode`). The self-contained path stays byte-for-byte unchanged, so
the ledger compounding experiment and the scaffolds carry zero regression risk.

The key structural fact that makes this cheap: the **execution core is identical**
in both strategies (sandbox worker, verify/integrity/browser gates, delivery,
PR). Only the **bookends** differ — how the worker **orients** (decides what to do
next) and how it **records** progress. Since the P3b demolition, orientation is
already delegated to the worker via its brief, so the second strategy is mostly a
brief variant + a progress-record swap + a done-detection swap + GitHub-issue read
plumbing.

## Clarifications

### Session 2026-08-14

- Q: On a github_issues repo, what should one devclaw "goal" map to? → A: **Both** —
  a goal can be a single issue (→ one PR → done) OR a whole milestone (devclaw
  works its issues across nights). A single standalone issue is a one-issue goal.
  P1 does single-issue first; milestone-spanning rides the same model.

**Deferred to a later clarify (implementation is not being planned yet):**
GitHub-unreadable failure posture (FR-008: cache-last-known vs block-the-tick);
whether `dispatch_issue` folds into P1 or stays P2; confirming the additive
"no constitution amendment" reading. These do not block recording the direction;
they block `/speckit-plan`, which is intentionally not being run now.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - github_issues strategy: devclaw works a curated backlog (Priority: P1)

As the owner of a real product repo where I curate GitHub issues and milestones,
I set that project's planning strategy to `github_issues`. When devclaw advances a
goal there, the worker **reads the linked GitHub milestone's open issues** (not a
PLAN.md), picks the next one by priority/order, implements it as one increment,
opens a PR that **closes that issue**, and records progress **as a comment on the
issue** — never touching a PLAN.md and never restructuring my milestones/backlog.
The goal is "done" when all the milestone's issues are closed and the done-gate
confirms.

**Why this priority**: This is the whole feature — the new strategy. It's what
makes devclaw usable on finance-sentry / lifekit-dashboard the way the owner
actually manages them.

**Independent Test**: On a test repo with a milestone holding two open issues,
run a `github_issues`-strategy goal to two increments; assert two PRs each
`Closes #N`, two issues closed, progress comments posted, no PLAN.md created, and
the milestone/issue structure otherwise unchanged.

**Acceptance Scenarios**:

1. **Given** a `github_issues` goal whose milestone has open issues, **When** the
   loop advances one increment, **Then** the worker's brief directs it to the
   milestone's issues (not PLAN.md), it implements the next issue, and the
   delivered PR body `Closes #<n>`.
2. **Given** that increment settles green, **When** progress is recorded, **Then**
   it is a comment on the worked issue (and/or the PR), and **no PLAN.md is
   created or modified** in the repo.
3. **Given** all of a milestone's issues are closed, **When** the loop
   re-evaluates, **Then** it proposes done, and the done-gate runs against the
   goal's `done_when` exactly as today.
4. **Given** a `github_issues` goal, **When** devclaw runs, **Then** it never
   creates, renames, reorders, closes-without-a-PR, or restructures milestones or
   issues it did not itself open — it only comments and closes-via-PR (the
   curation boundary).

---

### User Story 2 - self_contained strategy is unchanged (Priority: P1)

As the owner of a greenfield/experiment goal (ledger, a scaffold), I keep (or
default to) `self_contained`, and devclaw behaves exactly as it does today: the
worker maintains PLAN.md, the thin-advance loop reads the smallest not-yet-done
milestone, the slice guard and the PLAN.md-size tripwire apply.

**Why this priority**: The dial is worthless if adding it perturbs the working
model. Zero regression is a release gate, not a nice-to-have.

**Independent Test**: The existing PLAN.md/thin-advance/slice-guard test suite
passes unchanged against a `self_contained` (default) goal.

**Acceptance Scenarios**:

1. **Given** a goal with no strategy set, **When** it runs, **Then** it behaves as
   `self_contained` (today's default), byte-for-byte.
2. **Given** a `self_contained` goal, **When** the worker advances, **Then**
   PLAN.md, the slice guard, and the size tripwire all behave exactly as today.

---

### User Story 3 - selecting the strategy (Priority: P2)

As the operator, I set the strategy **per project** (the default for its goals)
and can **override per goal**, using the same shape as the existing dials
(`strictness`, `delivery_strategy`). A project registered with a curated backlog
gets `github_issues`; a greenfield build gets `self_contained`.

**Why this priority**: The strategy has to be selectable to be a dial, but the P1
stories can be validated with the field set directly; the ergonomic surface
(project registry field + per-goal override + the choice on create_goal) is the
second slice.

**Independent Test**: Register a project with `planning_strategy=github_issues`;
a goal created under it inherits `github_issues`; a per-goal override to
`self_contained` wins for that goal.

**Acceptance Scenarios**:

1. **Given** a project set to `github_issues`, **When** a goal is created without
   an explicit strategy, **Then** the goal runs `github_issues`.
2. **Given** that project, **When** a goal is created with an explicit
   `self_contained` override, **Then** that goal runs `self_contained`.
3. **Given** no project or goal setting, **When** a goal runs, **Then** the
   effective strategy is `self_contained` (safe default = today's behavior).

---

### Edge Cases

- **GitHub API unavailable / rate-limited on a `github_issues` goal**: the worker
  cannot read issue state. The loop must degrade legibly (a blocked/paused tick
  with an owner-actionable reason, or a cached last-known issue view), never
  silently invent a plan or fall through to writing a PLAN.md. [NEEDS
  CLARIFICATION: cache-last-known-and-proceed vs block-the-tick-until-API-returns
  — which failure posture?]
- **A `github_issues` goal whose milestone has zero open issues**: nothing to do →
  propose done (subject to the done-gate), not an error, not a fabricated task.
- **An issue with no acceptance criteria in its body**: the issue *is* the spec on
  this path, so a contentless issue yields an under-specified increment. The
  worker should surface this (a blocked/needs-answer signal or a comment asking
  for criteria), not guess — issue quality is the contract.
- **Two `github_issues` goals on the same repo touching overlapping files**:
  handled at the git/PR layer exactly as any two PRs (isolated workspaces per
  goal; the second PR conflicts at merge and is resolved/rebased). No new
  conflict surface from this feature.
- **A `self_contained` goal on a repo that also has GitHub issues**: allowed — the
  dial is explicit; a self_contained goal simply ignores the issues and uses
  PLAN.md.
- **Mixed history**: switching a project's strategy mid-life does not rewrite past
  artifacts; it changes how the *next* goal/increment orients.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A goal MUST carry an effective **`planning_strategy`** of
  `self_contained` (default) or `github_issues`, resolved as: explicit per-goal
  value, else the project default, else `self_contained`. Same resolution shape as
  the existing dials.
- **FR-002**: Under `self_contained`, orientation, progress-recording, and
  done-detection MUST be byte-for-byte today's behavior (PLAN.md spine, slice
  guard, PLAN.md-size tripwire). No observable change on this path.
- **FR-003**: Under `github_issues`, the worker's **orientation brief** MUST
  direct it to read the goal's linked GitHub milestone's open issues (via `gh` in
  the sandbox) and pick the next by priority/order — NOT to read or create
  PLAN.md.
- **FR-004**: Under `github_issues`, an increment's **scope/`done_when`** for the
  chosen issue MUST come from that issue's body (its acceptance criteria), and the
  delivered PR MUST `Close` that issue (the existing `_closes_issues` PR-body
  mechanism).
- **FR-005**: Under `github_issues`, **progress MUST be recorded as an issue
  and/or PR comment**; the worker MUST NOT create or modify a PLAN.md, and the
  PLAN.md-specific machinery (slice guard, size tripwire) is a no-op on this path.
- **FR-006**: Under `github_issues`, **done-detection** MUST be "all the goal's
  target milestone issues are closed," then the existing done-gate
  (`review_repository` against `done_when`) runs unchanged.
- **FR-007**: **Curation boundary** — on a `github_issues` goal devclaw MUST only
  *read* issues/milestones, *comment* on them, and *close* them via a merged PR
  (`Closes #N`). It MUST NOT create, rename, reorder, re-milestone, or bulk-close
  issues it did not itself open, nor restructure milestones. (Intake-doorway
  issue *creation* via `file_intake` is a separate, already-governed path and is
  out of scope here.)
- **FR-008**: The `github_issues` path MUST degrade **legibly** when GitHub is
  unreadable — a blocked/paused tick with an owner-actionable reason [or a cached
  last-known view], never a silent fallback to PLAN.md and never a fabricated
  plan. (Failure posture pinned in clarify — see Edge Cases.)
- **FR-009**: The execution core — sandbox worker turn-loop, verify/integrity/
  browser gates, fail-closed semantics, delivery, PR — MUST be identical across
  both strategies. This feature changes only the orient/record/done-detect
  bookends and the strategy field.
- **FR-010**: The zero-token idle guard MUST hold on both paths: an idle or
  blocked `github_issues` goal costs ~0 `claude` calls (issue reads are cheap
  `gh`/API calls gated behind the same should-advance checks, never LLM calls on
  an idle tick).
- **FR-011**: Strategy selection MUST be settable **per project** (default for its
  goals) and **overridable per goal**, surfaced on the read tools alongside the
  other dials.

### Key Entities

- **planning_strategy**: an enum on the goal (and a project default) —
  `self_contained` | `github_issues`. The dial this feature adds.
- **GitHub milestone (github_issues path)**: maps to a devclaw **goal** — a
  coherent multi-issue objective; its %-complete tracks goal progress.
- **GitHub issue (github_issues path)**: maps to one **increment** = one PR that
  `Closes` it; its body carries the increment's acceptance criteria (`done_when`).
- **PLAN.md (self_contained path)**: unchanged — the in-repo roadmap/progress
  spine.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a `github_issues` project, an operator can point devclaw at a
  milestone of curated issues and get one PR per issue (each closing its issue)
  with **no PLAN.md written to the repo** and **no change to the issue/milestone
  structure** beyond comments and PR-closes.
- **SC-002**: The full existing PLAN.md/thin-advance/slice-guard test suite passes
  **unchanged** against `self_contained` goals — zero regression on the default
  path (pass count ≥ baseline).
- **SC-003**: A `github_issues` goal whose milestone issues are all closed
  proposes done and is confirmed/denied by the **same** done-gate as today (no new
  completion path that bypasses grounded evaluation).
- **SC-004**: On both strategies, an idle/blocked goal makes **zero `claude`
  calls** per tick (the zero-token guard tests stay green).
- **SC-005**: With GitHub made unreadable mid-run, a `github_issues` goal produces
  a **legible blocked/paused state with an actionable reason**, never a silent
  PLAN.md fallback or a fabricated plan.

## Assumptions

- The sandbox worker has `gh` available and authenticated for the target repo
  (it already uses `gh` for PR creation); reading issues/milestones is within its
  existing capability.
- Companion mode remains the frame for `github_issues` repos: the owner curates
  the issues (their quality is the contract), devclaw executes them.
- `dispatch_issue(project, #N)` — a tool that turns a single GitHub issue into a
  goal — is the natural *entry* for this strategy but is **not** required for P1
  (a `github_issues` goal can be created against a milestone directly). Its
  disposition (fold in vs separate spec) is a clarify question.
- The mapping milestone=goal / issue=increment is the working model; a single
  standalone issue is simply a one-issue goal → one PR.

## Constitution Impact

Believed **additive — no amendment required**: the execution core, all gates,
fail-closed, the zero-token idle guard, and single-writer-to-state are unchanged;
`self_contained` is untouched; the new path is a second orientation/record
strategy behind a dial (the same shape ADR 0003 and the existing dials already
sanction). The spec asserts this explicitly; the clarify step confirms it, and if
any invariant text needs to name the new dial, that edit ships in the same arc.

## Direction Memory — Rejected Alternatives

*(Recorded per `.claude/rules/speckit-workflow.md`: the spec is the direction
memory.)*

- **Rejected: wholesale "delete PLAN.md, put everything on GitHub issues."** It
  breaks the self-contained experiments (ledger's PLAN.md + hidden-checklist
  grader), forces GitHub-issue overhead onto greenfield builds where it adds
  nothing, and discards a working model to chase a single use-case. The dial keeps
  both and matches devclaw's "one primitive, selectable dials" design.
- **Rejected: per-goal-only selection (no project default).** Rejected in favor of
  per-project default + per-goal override, matching `strictness` /
  `delivery_strategy` — a repo's relationship with devclaw is usually stable, so
  the project is the natural home for the default.
- **Rejected: letting devclaw restructure the human backlog on `github_issues`
  repos** (auto-milestoning, reordering, bulk-closing). Rejected: it steps on the
  owner's project management; devclaw reads/works/closes-via-PR only (FR-007).

## Slicing

- **P1 (firm — N PRs, sized in plan)**: the `planning_strategy` field + resolution
  (FR-001), the `github_issues` orient brief / progress-record / done-detection
  bookends (FR-003/004/005/006), the curation boundary (FR-007), the legible
  GitHub-unreadable degradation (FR-008), and the zero-regression guarantee for
  `self_contained` (FR-002) with named tests. Strategy *selection surface*
  (FR-011, US3) can ride P1 or immediately follow, but the field must be settable
  to test P1.
- **P2 (named, unsized)**: `dispatch_issue(project, #N)` entry tool (issue → goal).
- **P3 (named, unsized)**: richer issue-comment progress logs; milestone-level
  goal-progress surfacing on the console/read tools.
