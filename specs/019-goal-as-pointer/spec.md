# Feature Specification: Goal-as-Pointer

**Feature Branch**: `019-goal-as-pointer`

**Created**: 2026-08-25

**Status**: Draft

**Input**: User description: "A goal that works an issue references it structurally instead of pasting it: first-class issue references fetched fresh at dispatch, done_when defaulting to the issues' acceptance scenarios, an enforced length budget on the goal's own text, readiness-gated references, an explicit lane for issue-less goals — relocation, not deletion."

## Clarifications

### Session 2026-08-25

- Q: When `done_when` defaults to the issues' acceptance scenarios, are they
  read live at evaluation time or frozen at goal creation? → A: **Live at
  evaluation time.** The issue stays the single live source of truth end to
  end; grooming it mid-goal steers the finish line. The explicit
  `done_when` override remains the frozen-contract escape hatch. (Rejected:
  freezing at creation — reintroduces the frozen-copy staleness this spec
  kills; mid-goal fixes would force cancel+refile.)
- Q: Doorway violations (over-budget text, non-ready reference) — hard
  refusal, or warn-and-allow / override? → A: **Hard refusal, no override.**
  The machine holds the line the humans demonstrably didn't; the issue-less
  lane (US5) and grooming-then-retrying are the legitimate paths. (Rejected:
  an operator override flag — every override is a crack companion sessions
  would learn to reach for; warn-and-allow — the current regime with a log
  line. An emergency override can be specced later if evidence demands it.)
- Q: One issue → one live goal? → A: **Yes — exclusivity.** A reference to
  an issue already referenced by a live (not done/cancelled) goal is refused;
  refiling means cancelling the old goal first, matching the cancel+recreate
  doctrine and spec 007's single-claim semantics one layer earlier. (Rejected:
  warn-and-share — reintroduces double-dispatch risk with no observed need.)

## Why (context the requirements hang off)

Goals on this instance are essays: the 2026-08-24 pr-authorship goal carried
~500 words that duplicated its GitHub issue, corrected it inline, and froze
a PREREQUISITE that main independently fixed the same day (#681) — so the
night's worker re-solved a solved problem and shipped a conflicting PR
(#684). The essay pattern is scar tissue for a 45% per-goal first-pass rate
(audited 2026-08-25, spec 018), but it puts durable knowledge in the one
container that is frozen at authoring time and dies with the goal.

The architecture is already pointer-shaped everywhere else: worker-generic
instructions live in the runner's skill bundle, issues are graded for
readiness (specs 006/009), acceptance scenarios are executable ground truth
(spec 015), the worker plans itself in-sandbox (spec 008), and autonomous
pickup (spec 007) dispatches *issues*. The gap is that goal creation happily
accepts essays, so knowledge keeps landing in the wrong container. Doctrine
constraint: this is relocation, not deletion — the cut ships only together
with the doorway making the issue the place the context is REQUIRED to land.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - First-class issue references, fetched fresh at dispatch (Priority: P1)

As the operator, I create a goal by referencing one issue or an ordered list
of issues (repository + number) as structured data, plus at most a short
free-text note for ordering/scope glue. When the loop dispatches work for a
referenced issue, the worker's brief carries the issue's CURRENT title,
body, labels and state — fetched at dispatch time, never a copy frozen at
goal creation.

**Why this priority**: This kills the stale-contract failure class by
construction (the #684 night) and is the substrate every other story builds
on. It also resurrects the dispatch-boundary freshness guard idea (old
issue #393) in its natural home.

**Independent Test**: Create a goal referencing an issue; edit the issue;
trigger a dispatch; verify the brief carries the edited text. Close the
issue; trigger the next dispatch; verify no work is dispatched for it and
the goal log says why.

**Acceptance Scenarios**:

1. **Given** a goal referencing issue N, **When** the issue body is edited
   after goal creation and a dispatch then fires, **Then** the worker brief
   contains the post-edit body and not any creation-time copy.
2. **Given** a goal referencing issues [A, B] in order, **When** A's work
   settles and the next dispatch fires, **Then** B's current content is
   fetched and dispatched next.
3. **Given** a referenced issue that is CLOSED at dispatch time, **When**
   the dispatch boundary evaluates it, **Then** no worker session is spent
   on it: the item is treated as no-longer-needed, logged loudly, and the
   goal advances to the next item (or proposes done when none remain).
4. **Given** a referenced issue that cannot be fetched (deleted repo,
   permission, network), **When** dispatch prep runs, **Then** the goal
   blocks legibly with the existing lost-reference semantics — it never
   dispatches from a stale copy and never wedges the tick loop.
5. **Given** an idle goal with references, **When** ticks pass with no
   dispatch due, **Then** zero fetches occur — freshness work happens only
   at the dispatch boundary.

---

### User Story 2 - done_when defaults to the issues' acceptance scenarios (Priority: P2)

As the operator, when I create a referenced goal without writing a
`done_when`, the completion contract IS the referenced issues' acceptance
scenarios (the executable ground truth spec 015 established) — read live at
evaluation time, so grooming the issue also steers the finish line. A
hand-written `done_when` remains possible and overrides the default.

**Why this priority**: Removes the second hand-written contract that the
done-gate litigated for six rounds on the pr-authorship goal; the gate and
the worker converge on ONE source. Depends on US1's reference plumbing.

**Independent Test**: Create a referenced goal with no `done_when`; verify
the done-gate evaluates against the issues' scenarios; edit a scenario
mid-goal; verify the next evaluation honors the edited scenario.

**Acceptance Scenarios**:

1. **Given** a referenced goal created without `done_when`, **When** the
   done-gate evaluates, **Then** its verdict is grounded in the referenced
   issues' acceptance scenarios as they stand at evaluation time.
2. **Given** the owner edits an issue's acceptance scenarios mid-goal,
   **When** the next done-gate round runs, **Then** the edited scenarios are
   the contract — no frozen creation-time copy is consulted.
3. **Given** a referenced goal WITH an explicit `done_when`, **When** the
   done-gate evaluates, **Then** the explicit contract wins, unchanged from
   today.
4. **Given** a referenced issue with no recognizable acceptance scenarios,
   **When** goal creation runs, **Then** the doorway says so and refuses the
   scenario-default (the operator either grooms the issue or writes
   `done_when` explicitly) — no silent empty contract.

---

### User Story 3 - The length budget: essays rejected at the doorway (Priority: P2)

As the operator, when I (or a companion session) submit a referenced goal
whose free text exceeds the budget, creation is refused with an actionable
message naming where the content belongs: the referenced issue (via the
existing grading/regrade flow). The budget is configuration with a sane
default; goals without references are not subject to it (US5's lane).

**Why this priority**: This is the enforcement that changes authoring
behavior — preference alone demonstrably didn't (the operator has asked for
"small, sharp" repeatedly while essays kept landing). Mechanical rule at the
doorway, per the put-decisions-where-they-can-be-enforced ruling.

**Independent Test**: Submit a referenced goal with over-budget text and
verify refusal + message; resubmit within budget and verify acceptance;
verify an unreferenced goal of the same length is not blocked by this rule.

**Acceptance Scenarios**:

1. **Given** a referenced goal whose free text exceeds the budget, **When**
   creation is attempted, **Then** it is refused (nothing persisted) and the
   error names the referenced issue as the destination for the excess
   context and the flow that gets it there.
2. **Given** a referenced goal within budget, **When** creation is
   attempted, **Then** it succeeds unchanged.
3. **Given** the budget value changed via configuration, **When** the next
   creation is attempted, **Then** the new value applies.
4. **Given** an issue-less goal (US5 lane), **When** creation is attempted,
   **Then** this budget does not apply to it.

---

### User Story 4 - References are readiness-gated (Priority: P2)

As the operator, a goal can only reference issues the grading pipeline has
marked ready (the earned devclaw-ready state of specs 006/009). Referencing
an ungraded or needs-refinement issue is refused with a pointer to the
grading flow. This is where the relocated context is REQUIRED to land:
grooming the issue to readiness replaces authoring the essay.

**Why this priority**: Without it, the length budget just moves thin asks
into thin dispatches — the relocation half of the doctrine. With it, the
human-filed goal shape becomes exactly what spec 007 auto-pickup dispatches,
so autonomy inherits the same quality floor.

**Independent Test**: Reference a needs-refinement issue → refusal naming
the grading verb; grade it ready; reference it again → accepted.

**Acceptance Scenarios**:

1. **Given** an issue graded needs-refinement (or never graded), **When** a
   goal referencing it is created, **Then** creation is refused and the
   message names the grading flow that unblocks it.
2. **Given** the issue is subsequently graded ready, **When** creation is
   retried, **Then** it succeeds.
3. **Given** a referenced issue whose readiness is revoked mid-goal (label
   removed), **When** its dispatch boundary next evaluates it, **Then** the
   item does not dispatch and the goal surfaces the reason — consistent with
   US1's freshness semantics.

---

### User Story 5 - The issue-less lane stays open (Priority: P3)

As the operator, I can still create goals with no issue references — bench
runs, greenfield scaffolding, one-off experiments — through an explicit
lane whose behavior is today's: free-form objective, the existing firming/
scope-grill path, hand-written `done_when`. Nothing in this spec makes
issue-less work impossible; it makes essay-plus-issue work impossible.

**Why this priority**: Protects real use (shakedowns, new repos with no
backlog yet) from the new discipline; lowest risk, smallest change.

**Acceptance Scenarios**:

1. **Given** a goal created with no references, **When** it runs, **Then**
   its creation, firming, dispatch and done-gate behavior are unchanged from
   today (regression-pinned).
2. **Given** an operator submits an issue-less goal, **When** creation runs,
   **Then** the lane is an explicit choice (visible in the goal's record),
   not a silent fallback from a failed reference.

---

### Edge Cases

- Ordered references where a middle issue closes before its turn: skipped
  with a loud log entry (US1 sc.3); the order of the remainder holds.
- A reference to an issue in a repository other than the goal's project
  repository: refused at creation — a goal works one project's repo.
- Duplicate references in one goal: refused at creation.
- The same issue referenced by two live goals: refused for the second —
  one issue, one live goal (mirrors the single-claim rule of spec 007).
- Rate-limited or transiently failing fetch at dispatch: the dispatch
  attempt fails loud and mechanical (retried next tick under existing
  brakes); never silently dispatches stale content.
- An issue edited into emptiness (body deleted) after grading: dispatch
  boundary treats it like scenario 4's unfetchable case — block legibly,
  human decides.
- Legacy essay goals existing at upgrade time: unaffected — the rules bind
  at creation; existing goals run out under old semantics.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Goal creation MUST accept an ordered list of issue references
  (repository + issue number) as structured data, persisted on the goal —
  distinct from, and not parsed out of, free text.
- **FR-002**: At each dispatch boundary for a referenced item, the system
  MUST fetch the issue's current state and content, and the worker brief
  MUST be built from that fetch — never from a creation-time copy.
- **FR-003**: A referenced issue that is closed (or no longer ready, US4) at
  dispatch time MUST NOT be dispatched; the goal logs the reason and
  advances. A referenced issue that cannot be fetched MUST block the goal
  legibly under the existing lost-reference/human-gated semantics.
- **FR-004**: No reference fetch may occur on an idle tick — freshness work
  happens only at dispatch and done-gate evaluation boundaries. The
  zero-token guard tests extend to assert zero fetches on idle paths.
- **FR-005**: For a referenced goal created without `done_when`, the
  done-gate MUST ground its verdict in the referenced issues' acceptance
  scenarios read at evaluation time. An explicit `done_when` overrides. A
  referenced issue lacking recognizable scenarios fails creation of the
  scenario-default loudly (US2 sc.4).
- **FR-006**: Referenced goals MUST enforce a free-text length budget at
  creation: over-budget submissions are refused with an error naming the
  referenced issue as the content's destination and the grading/regrade flow
  as the route. The budget is a configuration value with one home and one
  default; issue-less goals are exempt.
- **FR-007**: Goal creation MUST refuse references to issues not currently
  graded ready, naming the grading flow. Readiness is re-checked at each
  dispatch boundary (FR-003's freshness applies to the label too).
- **FR-008**: Reference validity rules at creation: same-project repository
  only, no duplicates within a goal, no issue already referenced by another
  live goal.
- **FR-009**: Issue-less goal creation remains available as an explicit
  lane with today's behavior, pinned by regression tests; choosing it is
  recorded on the goal.
- **FR-010**: Every refusal in this feature is loud and actionable: the
  message states the rule, the offending input, and the verb that fixes it.
  No refusal path silently accepts-and-degrades.
- **FR-011**: Each behavior ships a named regression test; existing
  zero-token guard tests stay green; the stubbed suite exercises all fetch
  behavior through an injected fake (no network in tests).

### Key Entities

- **Issue reference**: repository + issue number, ordered position within
  its goal; validated at creation (ready, same-project, unclaimed, unique).
- **Dispatch-time issue snapshot**: the fetched current content threaded
  into one worker brief; ephemeral input, never persisted as contract.
- **Goal text budget**: one configured limit applied to referenced goals'
  free text at creation.
- **Lane marker**: the explicit referenced vs issue-less choice recorded on
  the goal.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A worker brief for a referenced item never contains issue
  content older than the dispatch that produced it (verified by the
  edit-then-dispatch test); creation-time copies are unrepresentable, not
  just avoided.
- **SC-002**: A goal whose referenced issue was resolved out-of-band spends
  zero worker sessions on it (the #684 class: reproduce the audited night's
  shape in a seeded test and show no dispatch fires).
- **SC-003**: 100% of newly created referenced goals have free text within
  budget — enforced, so measured as "no over-budget referenced goal exists
  post-feature".
- **SC-004**: Zero reference fetches on idle ticks (guard tests).
- **SC-005**: The per-goal first-pass rate (spec 018's corrected metric) is
  the outcome this feature exists to move toward its 0.70 threshold;
  tracked on the scorecard, not gated in this spec — direction, with the
  measurement already built.
- **SC-006**: An operator can go from "over-budget refusal" to an accepted
  goal using only the verbs the refusal message names (groom issue →
  regrade → recreate), without reading source code.

## Assumptions

- Spec 015's acceptance-scenario convention is the recognizable scenario
  format US2 depends on; an issue graded ready is expected to carry it, and
  US2 scenario 4 covers the ones that don't.
- The budget default is on the order of a short paragraph (hundreds of
  characters, not thousands) — the exact default is a plan-level choice in
  the config doorway; the spec's requirement is that it exists, is enforced,
  and is configurable.
- "Live goal" for the one-issue-one-goal rule means a goal that is neither
  done nor cancelled.
- The done-gate's evaluation-time scenario read rides the same
  dispatch-boundary freshness machinery (one fetch seam, two consumers).
- Spec 007's auto-pickup path is untouched but converges: after this
  feature, human-filed and auto-claimed work share one shape. The 007 flag
  stays off until the spec 018 ratchet earns it.
- Companion sessions (the operator's Claude sessions) are the main authors
  of goals today; the refusal messages are written to redirect THEM — an
  actionable machine-readable error is the behavioral lever.

## Rejected Alternatives (direction memory)

- **Advisory warning instead of hard refusal on over-budget text**:
  rejected — the preference-only regime was tried for weeks and essays kept
  landing; the operator's standing rule is to put invariants in mechanism,
  not judgment.
- **Parsing issue URLs out of free text** instead of structured references:
  rejected — keeps the essay container authoritative and makes freshness
  best-effort; structure is the point.
- **Snapshotting the issue into the goal at creation** (freeze for
  reproducibility): rejected — reproducibility of the ask is exactly the
  failure mode; the issue is live state and the platform keeps its own edit
  history.
- **Requiring references on ALL goals**: rejected — bench/greenfield work
  is real (US5); the discipline targets essay-plus-issue, not issue-less.
- **Enforcing via the scope-grill (cognition) instead of a mechanical
  check**: rejected for the budget/readiness/validity rules — those are
  invariants a length check and a label read can enforce for zero tokens;
  the grill remains the judgment surface for the issue-less lane.
