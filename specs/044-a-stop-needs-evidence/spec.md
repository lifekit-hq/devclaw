# Feature Specification: A stop needs evidence

**Feature Branch**: `044-a-stop-needs-evidence`

**Created**: 2026-09-09

**Status**: Draft — specified and clarified with Denys 2026-09-09 (four rulings, recorded below); SHRUNK at clarify from a seam registry + new guard to "every stop is a Problem, and a Problem carries evidence", riding the existing spec 031 seam and the existing strictness dial.

**Input**: User description: "A stop needs evidence: no sentence stops a goal without a fact devclaw can check. This week six goals were parked at six different judgment seams by text that outranked a checkable fact […] The invariant: every judgment seam declares the fact it must cite, the mechanical check of that citation, and its uncited direction: an uncited PASS fails closed, an uncited STOP fails open to the fact."

## North-star case *(mandatory — constitution 2.9.0)*

- **Failure moved**: **ran but needed the owner**, primarily; **stopped when it
  shouldn't**, secondarily. Every wedge below ended with an owner verb that was
  not a decision (a resume, a regrade, a steer, a hand-cleared field), and every
  one sat idle for hours to days first.
- **Number that shows it**: `interventions` on `get_scorecard_metrics`, 168h
  read of 2026-09-09: **35** interventions for **14** achieved goals
  (**2.5 per goal**), of which **12 resumes + 8 steers** are non-decision verbs.
  `get_loop_health`, same read: devclaw-caused idle **13.0 h of 44.7 h observed**
  (not-stuck rate **0.71**); top cause `mechanical:dispatch_cap` at 8.7 h, and a
  dispatch cap is what an unexplained stop turns into after two rounds.
- **Cut when**: US1 is cut if, 14 days after it lands, the `dropped_claim`
  count is zero AND no goal was parked on a stop a fact contradicted — the
  rule then costs a field nobody fills. US2 is cut if in the same window no goal
  reaches the done-gate with every pinned clause met and a structural finding
  (the path never fires). US3 is cut if no review refusal in the window cites
  only locations outside the judged span. The constitution clause stays in
  every case: it is the rule; the stories are its enforcement.

### north-star verdict: ADMIT (as shrunk at clarify)  -  spec 044

```
axis: owner · number: interventions 37 / 27 achieved = 1.37 per goal (336h, 2026-09-09), 20 of them non-decision (12 resumes, 8 steers) · cut when: dropped_claim = 0 over 14 days AND no goal parked on a stop a fact contradicted
owner after: FEWER actions - removes resumes on false env holds, regrades after false stale, blind `continue` on caps, rulings on shape; adds under `strict` one decision per unproven stop, with a default and a timebox (a decision is the owner's job)
domain: the protocol (what a verdict must carry) + the verdict of record (a fact outranks a claim) · Python outside: none · order: fact (the citation) first; the drop removes a brake, it adds none
class: a claim given the authority of a fact · boundary: the stop seam - 5 mechanisms there (blocked_kind taxonomy, the Problem seam, the churn park, the freshness-guard skip, the env hold + probe) -> REPLACES the silent skip and the strict structural hold with the Problem seam; adds no sixth
weight: 1 field on Problem, 1 marker (no evidence), 1 catalog row kind (dropped_claim), 0 env vars, 0 tables, 1 extension of an existing guard; deletes the strict structural hold path and the silent skip · reversible: yes, per story
SE: pass - layer 2 checks, layer 3 returns parsed citations, single writer kept, pass side fails closed (V), dropped claims logged (VI); V gains one clause and loses the strict structural hold (amend in the same arc)
AI-eng: pass - citation is one prompt line on the intake and review prompts (the done-gate already has it); the check is Python; zero tick-path cognition added; the evidence rule is a structural invariant, not a classifier, its A/B seam is the strictness dial; eval fixture required at plan: the fs#493 stale grade against its main snapshot
bullshit test: next week, goals that today are parked by a stale grade, a green-probe env report or a bare verify_cmd proceed; the owner's inbox holds design Problems and strict no-evidence Problems with a one-click default; list_problems shows dropped_claim rows > 0, or the story is cut

verdict reasons, strongest first:
1. The first draft (a registry of seven seams + a new guard family) would have been SHRINK: a second mechanism at a boundary that already has five. The clarify shrink to "every stop is a Problem, and a Problem carries evidence" passes the class test because it replaces two mechanisms and adds none.
2. Second-job test passes on the number, not on hope: 20 of 37 owner actions in 14 days were non-decision verbs on exactly the stops this spec drops or explains.
3. The spec says what it cannot do: it does not make a judge right. Making the judge right is spec 043 and the pinned rubric; this spec only refuses a judge that will not show its work.
```

## The class, stated once

Six goals were parked between 2026-09-03 and 2026-09-09 by a **claim given the
authority of a fact**. In each case a piece of text — a model verdict, a worker
report, a ticket's list, a caller-typed string, a glob list — changed the loop's
next move without being checked against the mechanical fact it contradicted:

| Where | The claim | The fact it outranked | What the owner saw |
|---|---|---|---|
| intake readiness (staleness axis) | `stale: true`, uncited | the defect at `CredentialEncryptionService.cs:35` on main; the issue saying nothing is implemented | nothing — the goal was silently skipped |
| pre-PR review gate (strict) | "leaves `EncryptedCredentials` un-rotated" | the schema: that table was dropped by an earlier migration | a dispatch-cap Problem two rounds later, cause not in it |
| done-gate structural axis (strict) | "structural: concerns", a new concern each round | ten pinned clauses met, three rounds running | a churn Problem asking him to rule on shape |
| worker env report | "sandbox lacks NODE_AUTH_TOKEN" (21 holds in 7 days) | doctor's probe for that credential reading green | a project hold, then resumes |
| create_goal `verify_cmd` | `dotnet test` typed into a chat call | the project manifest's declared gate | "dispatch cap 2 reached — review the open PRs" |
| change_class declared scope | a glob list | the ticket naming `frontend/package.json` in its own done-when | the same cap message, plus a steer that contradicted the ticket |

Six per-seam fixes in 14 days answer the same rule six times: #789 (the
done-gate refuses uncited current-code claims), spec 035 (pinned clause
rubric), #829 (provenance before wording for pauses), #891 (an env report
defers to the probe), #895, #896, #897 (filed 2026-09-09).

**What already exists, and is kept.** Spec 031 built the resolution half: a
blocked goal raises ONE typed Problem through ONE seam (`devclaw/goal/problems.py`)
— what is wrong, the clause, why the loop cannot decide it, bounded options, a
recommended default, a timebox — and the owner answers with `decide` or
`correct_implementation`; spec 041 makes every answer a recorded Decision the
next tick executes. `steer_goal` stays the direction-change verb and stays
refused while a Problem is open. Nothing in this spec touches that.

**What this spec adds** is the admission half, in one sentence: **every stop
is a Problem, and a Problem carries evidence.** The three failure shapes above
are exactly the ways a stop escaped that sentence — a Problem raised on a claim
no fact supported; a stop that raised no Problem at all; a Problem that named
the symptom and hid the cause.

## Two kinds of stop (ruled by Denys 2026-09-09, the spine of this spec)

- **Design stops are the owner's.** The world contradicts the plan: the worker
  finds that the provider's API does not expose what the design assumed, that
  two clauses of the contract cannot both hold, that the repo's mechanism
  contradicts the ticket. The worker's finding IS the evidence (its
  `BLOCKED: <conflict>` report, checkable as present in the run), the Problem
  is always raised in both modes with the finding and a recommendation, the
  owner answers, and the answer is a recorded Decision. Nothing in this spec
  drops a design stop. These are the stops Denys accepts and wants a history
  of.
- **Mechanical stops are devclaw's.** A missing token, a failing test, a cap,
  a gate refusing a path, a stale grade, a reviewer's memory of a table. A
  fact settles them - a probe, the tree, the manifest, the pinned clauses -
  never the owner. This week every one of these was either dressed up as a
  Problem and put in front of the owner, or stopped a goal with no Problem at
  all. That is the drift this spec removes.
- **A design stop that reaches the owner is a planning or grading defect
  (ruled by Denys 2026-09-09).** The fact that stopped the worker existed
  before dispatch - a provider capability, a prior finding in the repo's own
  log, a contradiction between two clauses - and the grade or the plan let the
  ask through without it. So the design Problem is raised and answered, and
  the answer records the stage that should have caught it (intake grade,
  admission lint, plan); a design stop recurring at the same stage is a
  grading defect to fix, never a decision to make twice. Good planning
  upstream, stubborn execution downstream (ruled 2026-08-21); this is the
  feedback edge that makes the planning stage improve from its own misses.
- **The quality bar is not the gate's opinion.** The bar is the pinned
  contract (spec 035) plus the project's own CI (spec 032). US2 lowers
  nothing on that bar: a structural "concern" was never a clause, and holding
  a close on it three rounds raised the intervention count, not the quality.
  The bar is raised where quality lives - a stronger contract, a stricter CI
  job in the repo - never by letting an uncited judgment park a goal.

## Clarifications

### Session 2026-09-09 (with Denys)

- Q: Once every pinned clause is met, what does the done-gate's structural
  axis do under `strict`? → A: **Advisory in both modes.** Structural
  findings attach to the close as follow-ups; they never hold a close once
  the pinned clauses are satisfied. (Alternatives held open and rejected:
  pin the concerns per revision like clauses — a second pin machinery; one
  bounded correction round — one round per goal, still a round on a judgment
  no rubric pins.)
- Q: Under `strict`, a review refusal whose blockers ALL cite locations absent
  from the judged span and the pre-run tree? → A: **Approve with
  advisories.** The dropped findings are attached to the task and the PR; the
  done-gate, which cites clauses, stays the close authority. (Rejected: one
  bounded re-review — a cognition call per bad refusal; fail closed as today —
  keeps the two-lost-rounds shape.)
- Q: Shape of the spec — a registry of seven judgment seams with its own
  structural guard, or ride the existing Problem seam? → A: **Every stop is
  a Problem with evidence.** No seam registry, no new guard family. Denys's
  concern, recorded: a registry cannot "solve the problem fully" and is a
  second mechanism at a boundary that already has one; what he wants is the
  agent proposing what the problem is with a recommendation, him answering,
  and a history of those answers — which is spec 031/041, made honest.
- Q: Is "every stop is a Problem" the same as "agents propose the problem
  with a recommendation, I answer, and we keep the history"? → A: **Yes on
  the resolution half, which already exists (spec 031 Problems, spec 041
  Decisions).** Denys's fear, recorded verbatim in substance: drift, and the
  quality bar getting lower. Ruled: a *design* stop (the plan cannot be built
  as designed, e.g. a provider API that does not expose the cards) is always
  raised to the owner with the worker's finding as evidence; a *mechanical*
  stop (no token, a failing test, a cap, a gate) is never the owner's and is
  settled by a fact. See "Two kinds of stop".
- Q: Should the automatic "drop an unproven stop and proceed" be switchable?
  → A: **Yes, on the existing strictness dial, not a new setting.** Under
  `strict` an unproven stop still raises the Problem, marked *no evidence*,
  with "drop and proceed" as the recommended default; the owner judges, and
  the timebox takes the default. Under `trust` the unproven stop is dropped
  without a Problem and logged. Per-project control is `strictnessDefault` in
  `devclaw.json`, which already exists; a second on/off knob beside the dial
  was rejected as two dials for one policy.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Every stop is a Problem, and a Problem carries evidence (Priority: P1)

Any path that stops a goal — a block, a hold, a readiness revocation that
skips it, a cap — raises a typed Problem through the one seam, and that Problem
carries **evidence**: a citation devclaw can check mechanically against a
snapshot it already holds (a repository path with line or symbol; a pinned
clause id; a probe id from the credential registry; a manifest key; a Decision
id) and a **recommendation**. A stop whose claim carries no citation that
passes its check is an *unproven stop*: under `trust` it does not stop the
goal — the claim is recorded as dropped and the loop proceeds as if it had not
been made; under `strict` it raises the Problem marked *no evidence* with
"drop and proceed" as the recommended default, so the owner judges and the
timebox takes the default. The structural guard that already asserts every
`mechanical:*` kind has a way back is extended to assert every stop path raises
a Problem and every Problem shape carries the evidence field.

**Why this priority**: it is the rule the other stories instantiate, and the
only story that catches the *next* seam: a stop that never reaches the Problem
seam, or reaches it without a fact, fails the build instead of a night.

**Independent Test**: exercise each stop path in the stubbed suite with a
claim and no citation; observe under `trust` that the goal is not parked and a
`dropped_claim` is recorded, and under `strict` that the Problem raised is
marked *no evidence* with the drop default. Add a stop path that bypasses the
Problem seam; the guard fails naming it.

**Acceptance Scenarios**:

1. **Given** the readiness grader returns `stale: true` with no cited path, or
   a path absent from the graded snapshot, **When** the grade is recorded,
   **Then** the issue's readiness is unchanged, the posted grade names the
   missing evidence, and a goal referencing the issue is not skipped (#896).
2. **Given** a worker reports `BLOCKED: env — <credential>` and the registry
   probe for that credential reads green, **When** the report settles,
   **Then** no project hold is raised, the report is recorded as superseded
   with the probe id (#891), and under `strict` the owner sees a Problem
   citing the probe with "proceed" recommended.
3. **Given** a goal is created with a verify command while the project's
   manifest declares one, **When** admission runs, **Then** creation is
   refused with the manifest command named and nothing is persisted (#897).
4. **Given** a ticket's body names a gate-input path in a backticked token,
   **When** the worker's change edits that path, **Then** the change_class
   gate judges it as product, whatever list classified the path (#895).
5. **Given** the dispatch cap is reached, **When** its Problem is raised,
   **Then** the Problem's *what* carries the mechanism of the last failure
   (the failing command and its first error line, or the gate and the path it
   refused), not only "cap reached — review the open PRs".
6. **Given** a goal is skipped because its referenced issue lost readiness,
   **When** the skip happens, **Then** a Problem is raised citing the grade
   that revoked it; a silent skip no longer exists.
7. **Given** a stop path is added that parks a goal without raising a Problem,
   or a Problem is raised without the evidence field, **When** the suite runs,
   **Then** the structural guard fails naming the path.
8. **Given** any seam's verdict is a **pass** with no citation, **When** it is
   evaluated, **Then** it still fails closed — the approval side is unchanged.

---

### User Story 2 - Pinned clauses met means the door is open (Priority: P2)

Once the done-gate confirms every pinned clause of the contract, the
structural axis no longer holds the close in either strictness mode. Its
findings become follow-ups recorded on the close, as they already are under
`trust`. Every other consequence of the `strict` dial is unchanged.

**Why this priority**: fs-431 spent three rounds and a `donegate_churn` park
on a judgment no rubric pins, after the gate had confirmed all ten pinned
clauses three times. Structural quality is the project's own lint and review
job (constitution IX); the gate's opinion of shape is advice, not a clause.
Ruled at clarify: advisory in both modes.

**Independent Test**: seed a goal under `strict` whose review confirms every
pinned clause and reports structural `concerns`; the goal closes on green CI
with the concerns attached as follow-ups and no `donegate_churn` park.

**Acceptance Scenarios**:

1. **Given** every pinned clause is satisfied and the structural axis reports
   `concerns` or `poor`, **When** the done-gate evaluates under `strict`,
   **Then** the verdict is `achieved`, the concerns are recorded as follow-ups
   on the close, and the goal proceeds to merge-on-close.
2. **Given** at least one pinned clause is unsatisfied, **When** the done-gate
   evaluates, **Then** the verdict is unchanged from today (the functional axis
   governs; structural findings are corrections only where they cite a clause).

---

### User Story 3 - A refusal points inside the change (Priority: P3)

A pre-PR review refusal (consulted under `strict`) blocks only through
findings that cite a location inside the judged span or the pre-run tree. A
finding naming a file, symbol, or table absent from both is dropped from the
verdict and reported as dropped. A refusal with no surviving blocker is an
approval with advisories (ruled at clarify).

**Why this priority**: issue-493's fix sat unpushed for two rounds on a
refusal naming a table the schema had dropped; the worker's next commit was a
test proving the target set from the schema rather than from the reviewer's
memory of the ticket.

**Independent Test**: feed the review gate a diff and a verdict whose only
blocker cites a path not in the span and not in the tree; observe the task
settle `done` with the finding recorded as dropped and shown on the PR.

**Acceptance Scenarios**:

1. **Given** a refusal whose blockers all cite locations absent from the span
   and the pre-run tree, **When** the gate settles, **Then** the change ships
   and the dropped findings are visible on the task and the PR.
2. **Given** a refusal with at least one blocker citing a location inside the
   span, **When** the gate settles, **Then** the task fails closed exactly as
   today, with the uncited findings marked dropped.

---

### Edge Cases

- **A citation that exists but does not support the claim.** The mechanical
  check is existence and shape (the path is in the snapshot, the clause id is
  in the pinned list, the probe id is registered, the manifest key is
  declared), never truth. The rule removes fabricated authority; it does not
  make the judge right. Making the judge right is spec 043 (the worker reads
  the loop's facts) and the pinned rubric (spec 035), not this spec.
- **A fact devclaw cannot check.** A worker names a gap no registered probe
  covers. The report is a proven stop by construction (its citation is the
  worker's own log line, checkable as present), the Problem is raised with the
  human vouch as the recommended option, and `resume_goal` stays the exit.
- **The judge cites the defendant's diary.** A cited path that exists only in
  the worker's own change is evidence for a claim *about that change* (the
  span is the thing judged) but never for a *stale* claim about main; the
  staleness seam checks its citation against the pre-run tree, not the span.
- **Owner text.** A `decide`, `correct_implementation`, or steer is not a
  stop and carries no evidence requirement: the owner's Decision is the one
  authority the constitution grants over the evaluator (spec 041, principle
  V). Nothing here checks owner text.
- **Two facts disagree.** A probe says green and the worker says missing: the
  probe wins (#891). A grader says stale and the tree says not: the tree wins.
  The mechanical snapshot is the tiebreak; no judgment gets a vote on another.
- **A stop with evidence that is still wrong.** Under `strict` the owner sees
  the citation and can `decide` against it; under `trust` the loop proceeds on
  it, as today. The spec does not promise fewer wrong stops with evidence; it
  promises no stops without it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Every path that stops a goal (block, hold, cap, readiness
  revocation that skips it, churn park) MUST raise a typed Problem through the
  one Problem seam. A silent stop is a defect.
- **FR-002**: The Problem type MUST carry an **evidence** field: a citation of
  a shape devclaw can check against a snapshot it already holds (repository
  path with line or symbol; pinned clause id; credential-registry probe id;
  manifest key; Decision id), and the name of the check applied.
- **FR-003**: A stop whose claim carries no citation that passes its check is
  an **unproven stop**. Under `trust` it MUST NOT park the goal: the claim is
  recorded as dropped and the loop proceeds. Under `strict` it MUST raise the
  Problem marked *no evidence* with "drop and proceed" as the recommended
  default, resolved by the owner or by the timebox.
- **FR-004**: A pass claim at any seam without a citation MUST continue to
  fail closed; this spec narrows nothing on the approval side.
- **FR-005**: The existing structural guard for mechanical kinds MUST be
  extended to fail the build on a stop path that does not raise a Problem, or
  on a Problem shape without the evidence field.
- **FR-006**: The intake readiness seam MUST cite the path (and line or
  symbol) that satisfies a `stale` verdict, checked for existence in the
  graded snapshot's file list; an uncited or unfound citation grades as not
  stale (#896 is this requirement's increment).
- **FR-007**: The worker environment-report seam MUST defer to a registered
  probe when one exists for the named gap; a green probe supersedes the
  report with the probe id (#891 landed this; the Problem it raises under
  `strict` is new).
- **FR-008**: A caller-supplied verify command MUST NOT shadow a manifest
  `verifyCmd`; admission refuses it with the manifest command named, and a
  bare tool name is refused on every project (#897).
- **FR-009**: A ticket's backticked path MUST count as declared scope for the
  change_class gate whenever the changed path was classified gate-input,
  whatever source classified it (#895).
- **FR-010**: The dispatch-cap Problem MUST carry the mechanism of the last
  failure in its *what* (the failing command's first error line, or the gate
  and path it refused), so the owner's `continue` is never blind.
- **FR-011**: The done-gate MUST NOT hold a close on the structural axis once
  every pinned clause is satisfied, in either mode; structural findings attach
  to the close as follow-ups.
- **FR-012**: A pre-PR review blocker MUST cite a location inside the judged
  span or the pre-run tree to block; uncited blockers are dropped and named on
  the task and the PR; a refusal with no surviving blocker is an approval with
  advisories.
- **FR-013**: The constitution MUST carry the rule as one clause under
  principle V: every stop is a Problem and a Problem carries evidence; an
  unproven stop is dropped under `trust` and judged under `strict`; an uncited
  pass fails closed; owner Decisions are the sole exception.
- **FR-014**: Every dropped claim MUST be countable: the problems catalog
  records a `dropped_claim` row per seam so the cut condition is read from
  `list_problems`, never remembered.
- **FR-015**: `steer_goal`, `decide`, `correct_implementation`, `resume_goal`
  and their refusal rules MUST be unchanged.
- **FR-016**: A design stop - a worker's `BLOCKED: <conflict>` report that
  the plan cannot be built as designed - MUST always raise a Problem in both
  modes, with the worker's finding as its evidence and a recommendation, and
  MUST never be dropped as unproven. Only mechanical and judgment stops are
  subject to FR-003.
- **FR-017**: The Decision that resolves a design Problem MUST record the
  stage that should have caught it (`intake_grade` | `admission` | `plan` |
  `none`), and `list_problems` MUST group design stops by that stage so a
  recurring miss at one stage is readable as a grading defect. The readiness
  grader and the dispatch-ready pass are instructed (one line each, no code)
  to ground an ask against the world it depends on - provider capabilities
  and prior findings recorded in the repository - not only against the code.

### Key Entities

- **Problem** (existing, spec 031): gains `evidence` (citation + check name)
  and, for unproven stops under `strict`, the marker *no evidence* and the
  recommended default `drop`.
- **Citation**: the evidence a claim carries. Shapes: repository path with
  line or symbol; pinned clause id; probe id; manifest key; Decision id.
  Checked for existence and shape by the named check.
- **Dropped claim**: a stop claim that lost its authority for want of a
  passing citation. Recorded with the seam, the claim text, the missing or
  failed citation, and the direction taken.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Non-decision interventions (resumes + steers) per achieved goal
  fall from 1.4 (20 of 14, 168h read of 2026-09-09) to at most 0.5 over the
  14 days after US1 lands.
- **SC-002**: Devclaw-caused idle share falls from 29% (13.0 h of 44.7 h) to
  under 10% of observed time over the same 14 days.
- **SC-003**: Zero goals parked for 24 hours or more by a stop that a
  mechanical re-check contradicts; every such stop appears as a
  `dropped_claim` row instead.
- **SC-004**: Zero silent stops: every goal that is parked or skipped in the
  window has an open or resolved Problem naming the mechanism.
- **SC-005**: No goal is parked `donegate_churn` with every pinned clause
  satisfied in the 14 days after US2 lands.
- **SC-007**: In the 14 days after US1 lands, no design stop recurs at a
  stage already recorded as the missed stage for the same class of fact
  (read from `list_problems` grouped by stage).
- **SC-006**: No review refusal in the 14 days after US3 lands cites only
  locations absent from the judged span.

## Assumptions

- The four per-seam fixes already filed or shipped (#891, #895, #896, #897)
  are the first increments of US1; US1 adds the evidence field, the unproven
  stop rule on the dial, the dispatch-cap mechanism line, the silent-skip
  Problem, the guard extension, and the constitution clause.
- "Checkable" means existence and shape against a snapshot devclaw already
  holds. No seam gains a new probe or a new cognition call; the zero-token
  idle guard is untouched because every check runs inside a seam that already
  spent its call.
- The pause classifier's provenance rule (#829) is unchanged; an account-wide
  pause is not a goal stop and raises no Problem.
- Spec 043 (the worker reads the loop's facts) is the worker-side half of the
  same principle and is unchanged: 043 lets the worker see the fact before it
  reports; 044 makes the report harmless when it is wrong anyway.

## Rejected alternatives

- **A registry of judgment seams with its own structural guard** (this spec's
  first draft). Rejected at clarify: a second mechanism at a boundary that
  already has one (the Problem seam), and it cannot make the judges right,
  which is the thing Denys feared it claimed. The rule survives; the
  enforcement rides the existing seam and the existing guard.
- **Patch the instances only (#895–#897 and stop).** Rejected: the seventh
  seam is unknown until it wedges a goal for days; the week's evidence is six
  seams in fourteen days, each found by an incident.
- **Make every unproven stop fail closed.** Rejected: that parks *more* goals
  on less evidence, the opposite of the north star; the safe direction is
  declared by the dial.
- **A separate on/off setting for the automatic drop.** Rejected: two dials
  for one policy; the strictness dial already exists per project.
- **Let the model self-certify its evidence.** Rejected: the judge grading its
  own citation is the smell itself; the check is mechanical or it is nothing.
- **Pin structural concerns per revision (US2 alternative).** Rejected at
  clarify: a second pin machinery for a judgment that is advice.
- **One bounded re-review on an all-dropped refusal (US3 alternative).**
  Rejected at clarify: a cognition call per bad refusal to reach the same
  answer.
