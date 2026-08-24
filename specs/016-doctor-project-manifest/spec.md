# Feature Specification: Instance Doctor + Per-Project Manifest

**Feature Branch**: `016-doctor-project-manifest`

**Created**: 2026-08-24

**Status**: Clarified 2026-08-24 (3 questions, answered by Denys: manifest =
`devclaw.json` at repo root; `verify_cmd` included in the manifest;
strictness precedence = most-specific-wins, resolved live)

**Input**: User description: "Instance doctor + per-project manifest (devclaw.json). Two interlocking features: (1) a mechanical, zero-LLM, read-only doctor diagnostic — an MCP tool (plus CLI entry) that probes deployed-instance invariants and per-project checks, reporting findings loudly without mutating anything — recovery stays existing verbs; (2) a repo-owned declarative manifest devclaw.json in each operated project — human-owned, PR-reviewed, read via one doorway module. Gate-relevant manifest settings read host-side at pre_run_sha. Doctor detects boilerplate/schema drift, re-onboard migrates via reviewable PR. Convention rider: a PR that changes persisted shape or in-repo boilerplate ships its doctor check."

## The problem

Every devclaw version transition (VPS redeploy) today ends with a hand-run
checklist reconstructed from memory: did the run window survive? is the
project→goal registry link still valid? is the OAuth credential still good? did
the skills bundle make it into the image? are there goal rows left in a shape
the new code no longer writes? Each item on that list is a **real past
incident** (run-window reset on redeploy; stale registry links after
cancel+refile; the 2026-07-20 night auth death; #610/#613 skills-bundle forks;
the #641 finding — auto-merge dead since the 008 shrink with tests none the
wiser). The fully-stubbed test suite structurally cannot see any of them: they
are *deployed-instance* drift, not code defects.

Separately, the boilerplate `onboard` installs into every operated repo
(AGENTS.md with `devclaw:managed` markers, README/ARCHITECTURE drafts,
`.devcontainer/Dockerfile`, the `.specify/` scaffold) carries **no version
stamp and no settings**, so "what vintage of devclaw boilerplate does this
repo carry, and under what settings does devclaw operate it?" is unanswerable
— and per-repo facts devclaw needs (app vs library surface for the browser
gate, stack markers, gate strictness default) are inferred heuristically
instead of declared, which is exactly how the dotnet-filter false-negative and
the finance-sentry-ui-library gate wedge happened.

This spec fixes the **class** (redeploy/upgrade drift + inferred-instead-of-
declared per-repo facts), not the instances.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Instance doctor (Priority: P1)

After a redeploy (or any time something feels off), the operator invokes one
`doctor` verb and receives, within seconds, a structured findings report over
the deployed instance: each named check with an ok / warn / fail verdict,
the evidence, and — for every non-ok finding — the existing recovery verb that
fixes it (`resume_goal`, `clear_usage_pause`, `link_goal`, re-onboard,
re-login). Doctor is mechanical (zero LLM calls), read-only (mutates nothing),
and loud (a check that cannot run reports itself as `unknown`, never silently
disappears from the report).

**Why this priority**: This is the codified post-redeploy checklist — the
direct version-to-version transition value, and it stands alone with no
manifest needed.

**Independent Test**: Seed an instance with each known drift condition (stale
registry link, expired credential timestamp, legacy-shape goal row, missing
skills bundle, run-window reset) and assert doctor reports each as a distinct
named finding with the right remedy verb; assert `FakeClaude.calls == 0` and
zero state mutation across the run.

**Acceptance Scenarios**:

1. **Given** a healthy instance, **When** doctor runs, **Then** every check
   reports ok and the report says so explicitly (no empty-output ambiguity).
2. **Given** a project whose goal link went stale after cancel+refile,
   **When** doctor runs, **Then** a named finding identifies the project and
   names `link_goal` as the remedy.
3. **Given** goal rows in a shape the current code no longer writes, **When**
   doctor runs, **Then** a legacy-shape finding names the rows and the
   migration path — doctor itself does NOT rewrite them.
4. **Given** a check whose probe errors (e.g. unreadable DB file), **When**
   doctor runs, **Then** that check reports `unknown`/fail with the error as
   evidence — the report never omits it.

---

### User Story 2 - Per-project manifest (Priority: P2)

Every operated repo carries a small human-owned JSON manifest (installed and
maintained by `onboard` via the same reviewable-PR path as the rest of the
boilerplate) that **declares** what devclaw today infers: manifest schema
version, the boilerplate revision the repo was last brought up to, the
project's gate-strictness default, its surface kind (app vs library, consumed
by the browser-gate applicability decision), stack markers, and the project's
verify command. Devclaw reads it through one doorway (one parse, one defaults
table, one precedence rule). Changes to the manifest arrive by PR like any
other repo change.

**Why this priority**: Turns fragile inference into reviewed declaration and
gives doctor's per-project checks something mechanical to verify. Depends on
nothing in US1 but is what makes US3 possible.

**Independent Test**: Onboard a repo → the PR carries a valid manifest;
dispatch against a repo whose manifest declares `surface: library` → the
browser gate treats the frontend diff as library surface without path
heuristics; tamper with the manifest from inside the sandbox mid-run → the
run's gates still use the pre-run version.

**Acceptance Scenarios**:

1. **Given** a repo with no manifest (pre-manifest project), **When** work is
   dispatched, **Then** instance defaults apply and nothing breaks; doctor
   flags the absence as a warn with re-onboard as the remedy.
2. **Given** a repo with a *malformed* manifest, **When** work is dispatched,
   **Then** the dispatch is rejected loudly with an actionable message — it
   never silently falls back to defaults.
3. **Given** a manifest declaring gate-relevant settings, **When** the
   sandboxed worker edits the manifest during a run, **Then** that run's gates
   read the pre-run-SHA version and the edit has no effect on them (named
   regression test).
4. **Given** a manifest whose schema version is newer than the running
   instance understands, **When** it is read, **Then** the read fails loudly
   ("instance too old for this repo"), never a silent partial parse.

---

### User Story 3 - Drift detection + guided migration (Priority: P3)

Doctor's per-project section compares each repo's manifest schema version and
boilerplate revision against the running instance, verifies `devclaw:managed`
marker integrity (paired, non-duplicated), and diffs the `.specify/` scaffold
against the packaged canonical source. (Scope refined during planning,
research R12: content drift of the LLM-authored prose docs is mechanically
undiffable — no canonical template exists — and is out of scope; revision
currency + marker integrity + scaffold drift cover the #610 fork class
mechanically.) Drift surfaces as findings
whose remedy is a re-run of `onboard`, which migrates the manifest and managed
blocks via a reviewable PR (preserving all human content outside markers) —
doctor detects, onboard migrates, the human merges. A process convention rides
along: a PR that changes persisted state shape or in-repo boilerplate ships
its doctor check, exactly as a behavior-change PR ships its named regression
test.

**Why this priority**: This is the compounding payoff — silent-fork drift
(#610's class) becomes detectable per repo, and future migrations get a
standing verification path — but it needs US1's report and US2's version
stamps to exist first.

**Independent Test**: Bump the boilerplate revision constant in code without
re-onboarding a fixture repo → doctor reports the repo behind with re-onboard
as remedy; hand-edit inside a managed block → doctor reports managed-block
drift; re-onboard → PR updates only marker-bounded content + manifest, doctor
goes green after merge.

**Acceptance Scenarios**:

1. **Given** a repo onboarded at boilerplate revision N while the instance
   ships revision N+1, **When** doctor runs, **Then** a per-project finding
   names the repo, both revisions, and re-onboard as the remedy.
2. **Given** a managed block whose content diverged from the canonical
   template, **When** doctor runs, **Then** a drift finding names the file and
   block.
3. **Given** a re-onboard on a drifted repo, **When** its PR merges, **Then**
   human-authored content outside markers is untouched and doctor reports the
   repo current.

---

### Edge Cases

- Doctor invoked while tasks are in flight: read-only by construction, so
  safe; in-flight state is reported as informational, never as drift.
- A project whose workspace directory was deleted out from under the registry:
  a per-project fail finding (the existing preflight semantics), not a doctor
  crash.
- Credential checks are mechanical (stored expiry / file presence), never a
  live cognition probe — doctor keeps the zero-LLM guarantee even for auth.
- Manifest present but committed only on a branch, not the default branch:
  manifest is read from the same ref the run itself uses; doctor reads the
  default branch.
- Two consecutive doctor runs with no state change produce identical reports
  (deterministic; no timestamps-as-findings noise).
- Instance older than a repo's manifest schema (repo leapfrogged the VPS):
  loud "instance too old" finding, mirror-image of the repo-behind case.

## Requirements *(mandatory)*

### Functional Requirements

**Doctor (US1)**

- **FR-001**: The system MUST expose a read-only `doctor` diagnostic on the
  operator surface (an MCP tool; a CLI entry point on the box) returning a
  structured report: per check — a stable check id, verdict (`ok` / `warn` /
  `fail` / `unknown`), human-readable evidence, and for non-ok verdicts the
  name of an existing recovery verb. Doctor MUST NOT mutate any state.
- **FR-002**: The instance section MUST cover at least: code-vs-persisted
  state-shape version agreement (including rows in shapes the current code no
  longer writes), credential presence/expiry (mechanically), skills-bundle
  presence, run-schedule/window integrity across redeploy, usage-pause state,
  and project→goal registry-link integrity.
- **FR-003**: The per-project section MUST cover at least: workspace
  preflight status, manifest presence + validity + schema-version
  compatibility, boilerplate revision currency, `devclaw:managed` marker
  integrity, and `.specify/` scaffold drift against the packaged canonical
  source (scope per research R12).
- **FR-004**: Doctor MUST make zero cognition calls on every path (guarded by
  a `FakeClaude.calls == 0` test) and MUST NOT run on the heartbeat tick path
  — it is on-demand only.
- **FR-005**: A check that cannot execute MUST appear in the report as
  `unknown`/`fail` with the error as evidence — never be silently omitted; a
  fully healthy instance MUST be reported affirmatively.

**Manifest (US2)**

- **FR-006**: Each operated repo carries a human-owned JSON manifest —
  **`devclaw.json` at the repo root** (clarified 2026-08-24) — containing at
  minimum: manifest schema version, a published `$schema` reference,
  boilerplate revision, gate-strictness default, surface kind
  (`app` / `library`), stack markers, and the project's **`verify_cmd`**
  (clarified 2026-08-24: included — it is human-owned and PR-reviewed input,
  unlike the removed #233 planner override which was model output; FR-009's
  pre-run-SHA rule is what makes its inclusion safe).
- **FR-007**: The manifest MUST be read through exactly one doorway module —
  one parse, one schema validation, one defaults table, one precedence rule —
  mirroring the `config.py` single-doorway doctrine. No second reader
  interprets manifest bytes independently.
- **FR-008**: Precedence for settings that also exist at goal level
  (strictness) is **most-specific-wins, resolved live** (clarified
  2026-08-24): an explicit per-goal setting > the manifest default > the
  instance default, evaluated at each read — merging a manifest change
  immediately affects goals that never had an explicit setting.
  `set_goal_strictness` remains authoritative for a goal it was called on.
  Rejected: snapshot-at-goal-creation (stale until cancel+recreate);
  manifest-always-wins (silently deprecates the existing per-goal verb).
- **FR-009**: Gate-relevant manifest settings MUST be read host-side from
  state the worker cannot write. *(Strengthened during implementation, PR 2:
  the read is pinned to the repo's REMOTE DEFAULT-BRANCH TIP — the
  human-merged truth — rather than the originally-specified pre-run SHA. The
  pre-run SHA sits on the goal branch, so a PRIOR task's worker commit could
  still have carried a gate-weakening manifest edit into the next task's
  baseline; the merged base closes within-run AND cross-task tamper in one
  rule. Workspaces with no remote — dev/stub — fall back to the worktree,
  which is there the only truth.)* Edits made to the manifest by the worker
  (worktree or goal-branch commit) MUST have no effect on any gate (named
  regression test — the #358/#233 worker-routes-around-constraints class).
- **FR-010**: An absent manifest yields instance defaults plus a doctor warn;
  a malformed manifest fails dispatch loudly with an actionable message —
  never a silent fallback. A manifest schema version newer than the instance
  fails loudly as "instance too old".
- **FR-011**: `onboard` installs and updates the manifest as part of its
  reviewable PR; devclaw never writes into the human-owned manifest outside
  that path. Any devclaw-authored stamp lives in a separate generated artifact
  or a marked managed block — never mixed into human-owned settings (the #617
  no-second-writer rule).

**Drift + migration (US3)**

- **FR-012**: The boilerplate revision is a single constant in code, bumped
  when installed boilerplate content changes; doctor compares it against each
  repo's manifest.
- **FR-013**: Re-onboard on a drifted repo migrates the manifest and managed
  blocks via a reviewable PR, preserving all content outside
  `devclaw:managed` markers; a repo needing no migration re-onboards to a
  no-op (no PR).
- **FR-014**: The repo's contribution docs (CLAUDE.md conventions) gain the
  rider: a PR that changes persisted state shape or in-repo boilerplate ships
  its doctor check in the same PR.

### Key Entities

- **Doctor report**: the full diagnostic output — instance section +
  per-project sections; deterministic for unchanged state.
- **Finding**: one check's result — stable id, verdict, evidence, remedy verb.
- **Project manifest**: the human-owned per-repo declaration — schema
  version, boilerplate revision, per-project settings.
- **Boilerplate revision**: the code-side constant naming the current vintage
  of installed boilerplate.
- **Managed block**: `devclaw:managed`-marker-bounded content in an operated
  repo — devclaw-owned, diffable against canonical templates.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The post-redeploy verification that today lives in session
  memory is one tool call completing in under 30 seconds.
- **SC-002**: Every historical drift-incident class named in this spec
  (run-window reset, stale registry link, expired credential, missing skills
  bundle, legacy-shape rows, managed-block fork) is detectable by a named
  doctor check — demonstrated by a seeded-fault test per class.
- **SC-003**: Doctor makes zero cognition calls and zero state writes on all
  paths (guard tests hold).
- **SC-004**: Every repo onboarded after this ships carries a valid manifest;
  dispatch against a `library`-declaring repo never triggers the app-surface
  browser-gate expectation.
- **SC-005**: A mid-run manifest edit from inside the sandbox demonstrably
  does not alter that run's gate behavior (named regression test).
- **SC-006**: Every non-ok finding names a concrete remedy verb — no finding
  is reported without a next action.

## Assumptions

- Doctor's MCP tool is the primary surface (waiter-driven, like every other
  operator verb); the CLI entry is a thin wrapper for box-local use.
- Doctor v1 is report-only: it gates nothing and blocks nothing. Wiring it
  into deploy automation is future work, deliberately out of scope.
- Credential "validity" is judged mechanically (presence, stored expiry) —
  a live probe stays the existing auth-probe path, out of doctor's scope.
- The manifest carries no runtime state (in-flight refs, schedules, pause
  state) — that stays in the instance store under the single-writer CAS.
- Per-goal `set_goal_strictness` remains; the manifest supplies a default,
  not a lock (exact precedence pending clarification).
- The existing dispatch-time workspace preflight (spec 003) is reused as the
  per-project workspace check, not reimplemented.

## Rejected alternatives (direction memory)

- **Doctor as migrator** (detect → auto-heal): rejected. The moment doctor
  writes state it becomes a second writer; migrations stay where #617 put
  them (one-time, in code, at store construction) and boilerplate migration
  stays in re-onboard behind a reviewable PR.
- **Stamp + settings in one file**: rejected — devclaw writing into the
  human-owned manifest recreates the two-writer failure mode #617 killed.
- **A separate `project_doctor` tool**: rejected — per-project checks are a
  section of one report; two tools would fork the "is the instance healthy?"
  answer.
- **LLM-backed doctor checks** ("ask the model if the repo looks right"):
  rejected — doctor is mechanism; cognition-shaped repo judgment already has
  homes (intake grading, review_repository).
- **Manifest as devclaw-generated view**: rejected — views are never read
  back for decisions; a settings file devclaw both wrote and obeyed would be
  self-licensing.
