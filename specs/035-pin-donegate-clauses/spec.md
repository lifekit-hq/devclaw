# Feature Specification: Pinned done-gate clauses — decompose the contract once per revision

**Feature Branch**: `035-pin-donegate-clauses`

**Created**: 2026-09-05

**Status**: Draft

**Input**: User description: "Done-gate: decompose the completion contract ONCE per contract revision and pin the clauses — re-decomposing every round makes closing a moving target (lifekit-hq/devclaw issue #793, the fs-479 two-hour-close class)"

## Why (context the requirements hang off)

Every done-gate round today re-decomposes `done_when` from scratch inside the
evaluator call. Observed on fs-479 (2026-09-01, the two-hour close): the same
contract yielded 4 clauses at 10:23, 6 at 11:06, and 8 at 12:24 — and each
fresh decomposition scrutinized a new corner. A clause judged *satisfied* at
11:06 became the sole failure at 12:24 with no relevant repo change in
between. At ~25 minutes per round, a goal that is materially done circles
instead of converging. The churn brake (`donegate_churn`) catches the
symptom; the cause is that the judge re-derives its rubric every round.

The class is the task_change doctrine applied to the contract: **one
definition of the contract, like the one definition of the change**. The
rubric must be stable across rounds so gate progress is monotonic — clauses
flip to satisfied and stay counted; rounds shrink the open set instead of
reshuffling it. The revision key already exists mechanically: every round
computes a content digest of the live contract
(`done-gate contract … — revision <hash>`), and per-round progress is
already persisted (`goal_status.donegate_progress`, the churn brake's
progress-awareness). This spec pins the missing piece between them: the
clause list itself. This is the scorecard's failing metric — first-pass
convergence 0.36 against the 0.70 ratchet (2026-09-05 reading) — and its
direct cause.

## Clarifications

### Session 2026-09-05

- Q: What makes a clause "the same clause" across rounds — how is clause identity fixed at pin time? → A: A stable id plus verbatim clause text assigned once at pin time; every later round's verdict must reference clauses by id; accounting is keyed on id (monotonicity becomes a mechanical id-set check, not a text comparison).
- Q: Besides a contract amendment changing the revision digest, should the operator have a way to force a re-decomposition when the pinned rubric turns out bad? → A: Amendment-only — no new verb. The sole re-pin trigger is a changed revision digest; a bad rubric is fixed by editing the contract text on the tracker, which re-pins as a side effect. Keeps the contract-on-the-tracker truth and adds no verb surface.
- Q: How is the pinned clause list stored — history per revision, or latest-only per goal? → A: History — one persisted row per (goal, revision digest), old pins retained for audit; each round reads the row matching the current digest. An amendment mid-goal leaves the prior rubric inspectable.
- Q: How does a clause settled by a recorded Decision (spec 031) count in the gate's arithmetic? → A: It stays in the pinned denominator, counted satisfied with the Decision cited as its evidence. The denominator never changes within a revision; a Decision is evidence, never rubric surgery.
- Q: When the evaluator believes a pinned clause itself is wrong (unfalsifiable, or a design conflict in the contract), what may it do? → A: Judge it as written; the only escape is the existing lane — a needs_human verdict raising a typed Problem (spec 031) that names the clause id. Resolution is a contract amendment (which re-pins) or a Decision. The judge never edits the rubric.
- Q (review round): May a previously-satisfied clause flip back to unsatisfied freely? → A: No — a flip requires a rationale citing what changed since the satisfying evidence (a repo change in the span, or a named defect in the prior evidence). Flips stay possible (regressions are real) but become expensive and legible.
- Q (review round): Does a re-pin discard the accounting of clauses the amendment didn't touch? → A: No — a new clause byte-identical to one in the prior pin inherits its satisfied-with-evidence status (still re-judgeable under the flip rule). Only genuinely changed clauses start open.
- Q (review round): Does a malformed/unreviewable round count toward the churn brake? → A: No — it is a mechanism failure (#186 class), not a judgment round; it never increments the churn counter.
- Q (review round): Where is decomposition QUALITY addressed? → A: Outside this spec, by name: the done-gate calibration eval set is the companion measurement. Pinning bounds the damage of a bad decomposition (visible, auditable, amendable — once per revision); the eval set measures and improves the decomposition itself.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The rubric is derived once and reused (Priority: P1)

The first done-gate evaluation of a given contract revision decomposes
`done_when` into a clause list exactly once and persists it keyed on that
revision. Every later round for the same revision judges exactly the
persisted list — evidence is re-gathered fresh each round; the rubric is
not re-derived.

**Why this priority**: This is the fix itself. Without the pin, every other
improvement to the gate is judged against a moving target.

**Independent Test**: Drive one goal through three done-gate rounds against
an unchanged contract (stubbed evaluator). The decomposition happens on
round 1 only; rounds 2 and 3 receive the identical clause list; the gate's
recorded clause count is constant across all three rounds.

**Acceptance Scenarios**:

1. **Given** a goal whose contract revision has no pinned clause list,
   **When** the done-gate runs its first round, **Then** the contract is
   decomposed into clauses, the list is persisted keyed on the revision
   digest, and the round is judged against that list.
2. **Given** a pinned clause list for the current revision, **When** any
   subsequent round runs, **Then** no re-decomposition occurs and the round
   judges exactly the pinned clauses — same identities, same count — while
   evidence for each clause is gathered fresh.
3. **Given** the fs-479 shape (a contract that previously yielded 4, then 6,
   then 8 clauses), **When** three rounds run against one revision, **Then**
   the clause count is identical in all three rounds.

---

### User Story 2 - Gate progress is monotonic and legible (Priority: P2)

Round over round within one revision, a clause judged satisfied with cited
evidence keeps its identity and stays counted; the gate's refusal note names
only clauses from the pinned list; the churn brake's progress counter
(`donegate_progress`) counts satisfied clauses against the pinned list's
stable denominator.

**Why this priority**: Pinning the list (US1) removes re-derivation; this
story makes the convergence visible and enforceable — the open set shrinks
or the goal blocks honestly, and the operator can read WHICH clauses remain
without re-interpreting a reshuffled rubric.

**Independent Test**: Stub a three-round sequence where rounds satisfy
clauses A, then A+B, then A+B+C of a pinned five-clause list. The recorded
progress is 1, 2, 3 against a constant denominator of 5, and each refusal
rationale names only unsatisfied clauses from the pinned list.

**Acceptance Scenarios**:

1. **Given** a clause judged satisfied with cited evidence in round N,
   **When** round N+1 runs against the same revision, **Then** that clause
   appears in the round's accounting with the same identity (it can be
   re-judged on fresh evidence, but it cannot vanish or be renamed).
2. **Given** a round that refuses to close, **When** its rationale is
   recorded, **Then** every clause the refusal names is a member of the
   pinned list, quoted by its pinned identity.
3. **Given** the churn brake's progress check, **When** rounds run under one
   revision, **Then** the satisfied-clause count is measured against the
   pinned denominator, so a genuinely converging goal is never parked as
   churn by a rubric reshuffle.

---

### User Story 3 - A contract amendment re-pins exactly once, with Decisions carried forward (Priority: P3)

When the live contract changes (an issue amendment changes the revision
digest), the next round re-decomposes exactly once, says so in its
rationale, and pins the new list. Clauses covered by recorded Decisions
(spec 031 — an operator `decide`/`correct_implementation`) remain settled
facts wherever the amended contract still contains them: a Decision is
never re-litigated by a re-pin.

**Why this priority**: Amendments are the legitimate way the rubric changes
(the contract is live-editable on the tracker by design, spec 024/FR-005).
Without this story the pin would either freeze a stale rubric or silently
re-derive — both wrong. Carried-forward Decisions keep spec 031's "the gate
never re-asks a decided clause" true across re-pins.

**Independent Test**: Amend the referenced issue's acceptance section
between rounds (stubbed fetch returning a new digest). The next round logs
exactly one re-decomposition naming the revision change; a clause decided
via `decide` before the amendment, still present in the amended contract,
is not re-asked.

**Acceptance Scenarios**:

1. **Given** a pinned list for revision R1, **When** the live contract's
   digest changes to R2, **Then** the next round decomposes once, persists
   the R2 list, and its rationale names the revision change as the reason.
2. **Given** rounds continuing under R2, **When** they run, **Then** no
   further decomposition occurs until the digest changes again.
3. **Given** a Decision recorded against a clause under R1, **When** the R2
   list still contains that clause's requirement, **Then** the Decision
   still forecloses re-asking it (spec 031 semantics survive the re-pin).

---

### Edge Cases

- **Decomposition itself fails** (crash, unparseable response): the round is
  unreviewable and fails closed for that round — #186 semantics; nothing is
  pinned, the next round attempts decomposition again. A crash never pins a
  garbage rubric.
- **Pinned list lost or corrupt at read time**: loud, legible degrade — the
  round re-decomposes, records that it did so and why (corrupt/missing pin),
  and re-pins. Silent judgment against a half-read list is forbidden.
- **Ceremony-clause drop (evaluator step 1a)** happens at decomposition
  time, once per revision: dropped ceremony clauses are recorded with the
  pinned list (named once), not re-discovered and re-dropped every round.
- **Goals already mid-churn at deploy**: no migration — the first round
  after deploy finds no pin for the current revision, decomposes once, and
  proceeds. Existing `donegate_progress` values remain valid as
  best-seen counts.
- **Explicit (non-pointer) `done_when`**: the revision digest is the content
  hash of the goal's own stored contract text — the pin works identically;
  such contracts change only by cancel+recreate, so re-pins simply never
  trigger.
- **Evaluator disputes a pinned clause** (unfalsifiable, or a design
  conflict in the contract): it judges the clause as written and may
  escalate only through the existing lane — a `needs_human` verdict raising
  a typed Problem (spec 031) naming the clause id. It never skips, edits,
  or re-derives the clause (clarified 2026-09-05); resolution is an
  amendment (re-pin) or a Decision.
- **Strictness dial**: untouched. The pin governs WHAT is judged; `trust` vs
  `strict` keeps governing what a structural concern does. The done-gate
  stays always-hard.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The done-gate MUST decompose a completion contract into its
  clause list at most once per contract revision (the existing content
  digest of the live contract), and MUST persist the pinned list as one
  record per (goal, revision digest), with prior revisions' pins retained
  for audit (clarified 2026-09-05). Each round reads the record matching
  the revision it is judging.
- **FR-002**: Every done-gate round MUST judge exactly the pinned clause
  list for the current revision: each clause carries a stable id and its
  verbatim text, assigned once at pin time; the evaluator receives the ids
  and MUST reference clauses by id in its verdict, gathering fresh evidence
  per clause but never adding, removing, merging, splitting, or renaming
  clauses. A verdict referencing an unknown id is malformed and fails the
  round closed (#186).
- **FR-003**: A changed revision digest MUST trigger exactly one
  re-decomposition, and that round's rationale MUST name the revision change
  as the cause. No other event triggers re-decomposition — no operator verb
  exists for it (clarified 2026-09-05: a bad rubric is fixed by amending the
  contract text, which changes the digest) — except the corrupt/missing-pin
  recovery in FR-006. On a re-pin, a clause in the new list byte-identical
  to a clause in the prior revision's pin MUST inherit that clause's
  satisfied-with-evidence status (still re-judgeable under FR-011); only
  genuinely changed clauses start open — an amendment never resets
  accounting it did not touch.
- **FR-004**: Gate accounting MUST be monotonic within a revision and keyed
  on clause id: the progress counter measures satisfied ids against the
  pinned denominator; refusal rationales name only pinned ids (with their
  verbatim text); a satisfied clause keeps its id in every later round
  (re-judgeable on evidence, never re-derivable out of existence).
- **FR-005**: Ceremony-clause decomposition drops (evaluator step 1a) MUST
  occur at pin time, be recorded alongside the pinned list, and not recur
  per round.
- **FR-006**: A missing or unreadable pin at round time MUST degrade loudly:
  the round re-decomposes, re-pins, and records that recovery in its
  rationale. An unparseable decomposition attempt fails the round closed
  (#186) and pins nothing. A malformed or unreviewable round (crash,
  unparseable verdict, unknown clause id) is a mechanism failure, not a
  judgment: it MUST NOT increment the churn brake's round counter.
- **FR-007**: Recorded Decisions (spec 031) MUST survive a re-pin: a decided
  clause whose requirement persists in the amended contract is not re-asked.
  Within a revision, a decided clause stays in the pinned denominator and is
  counted satisfied with the Decision cited as its evidence (clarified
  2026-09-05) — the close rationale names how each clause was met, and no
  Decision ever shrinks the rubric.
- **FR-008**: The persisted-state shape change MUST ship its doctor check in
  the same arc (spec 016 FR-014 convention): the pin store exists, every pin
  row keys to a real goal, and the active revision's pin parses.
- **FR-009**: The zero-token idle guard and the always-hard status of the
  done-gate are untouched: pinning happens only inside an already-running
  done-gate round; nothing new runs on the idle tick path.
- **FR-010**: The invariant this spec adds — one decomposition per revision,
  monotonic accounting against it — MUST be pinned by named tripwire tests
  in the fail-closed-gate class (extending the existing done-gate tests, not
  minting siblings).
- **FR-011**: A clause previously recorded satisfied with cited evidence MAY
  flip to unsatisfied in a later round of the same revision ONLY with a
  rationale citing what changed: a repo change in the span since the
  satisfying evidence, or a named defect in that evidence. A flip without
  such a citation is a malformed verdict (FR-006 semantics). Regressions
  stay catchable; free flip-flopping does not.

### Key Entities

- **Contract revision**: the existing content digest of the live contract
  text (issue-scenario contract for pointer goals; the stored `done_when`
  for explicit goals). Already computed every round; becomes the pin key.
- **Pinned clause list**: the decomposition of one revision — ordered
  entries each carrying a stable clause id and the verbatim clause text,
  plus the ceremony-drop record — persisted once, read every round,
  replaced only by a revision change (or FR-006 recovery).
- **Round accounting**: per-round judgment over the pinned list — satisfied
  set, open set, cited evidence — feeding `donegate_progress` and the churn
  brake with a stable denominator.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For any goal and any unchanged contract revision, the clause
  count and clause identities recorded by consecutive done-gate rounds are
  identical — the rubric-drift half of the fs-479 signature (4 → 6 → 8
  clauses under one revision) is structurally impossible, verified by the
  named tripwire test. The verdict-instability half (a satisfied clause
  flipping without cause) is bounded by FR-011: every flip carries a cited
  cause or fails the round as malformed.
- **SC-002**: A clause recorded satisfied in round N appears with the same
  identity in round N+1's accounting (same revision) in 100% of rounds —
  no satisfied clause ever vanishes from the rubric.
- **SC-003**: Re-decomposition events per goal equal exactly the number of
  distinct contract revisions it was judged under (plus any explicitly
  recorded FR-006 recoveries) — observable from gate logs over a live
  multi-night window.
- **SC-004**: First-pass convergence on the live scorecard moves toward the
  0.70 ratchet threshold over the following 14 active nights, and no goal
  parks as `donegate_churn` while its satisfied-clause count is rising
  against a stable denominator. Decomposition quality itself is measured by
  the done-gate calibration eval set (the named companion to this spec, not
  part of it), and SC-004's convergence movement is read alongside that
  measurement.
- **SC-005**: Zero-token idle guards and always-hard done-gate guards stay
  green throughout (`FakeClaude.calls == 0` on idle paths; unreviewable
  rounds still fail closed).

## Assumptions

- The existing revision digest (content hash of the live contract, logged as
  `done-gate contract … — revision <hash>`) is the correct and sufficient
  pin key; no second notion of contract version is introduced.
- The evaluator remains a single cognition call per round; pinning changes
  its input (the clause list is supplied, not re-derived), not the call
  cadence. No new cognition calls are added anywhere.
- `donegate_progress` (the churn brake's progress-awareness) remains the
  progress store; this spec gives it a stable denominator rather than
  replacing it.
- Spec 031's Problems/Decisions machinery is present and its "never re-ask a
  decided clause" behavior is the semantic this spec must preserve across
  re-pins.
- Goal-branch delivery, merge-on-close (spec 025), and the strictness dial
  (ADR 0007) are untouched.
- The done-gate calibration eval set (known-good closes that must pass first
  try, known-bad closes that must hold, graded against pinned rubrics) is
  built as a companion workstream — evals-only, no spec artifact needed. It
  is the measurement for the decomposition-quality residual this spec
  deliberately does not mechanize.

## Rejected alternatives (direction memory)

- **Tune the churn brake instead** (wider windows, smarter reset): treats
  the symptom — the brake would still be parking goals that a stable rubric
  would have closed. The brake stays as the backstop; the cause is fixed at
  the rubric.
- **Prompt-only fix** ("decompose consistently" instruction, no persistence):
  an instruction cannot make two independent cognition calls agree; only a
  persisted artifact can. Same lesson as task_change (#630): the answer is
  computed once, mechanically, not re-derived carefully.
- **Freeze the contract text itself at first judgment**: contradicts spec
  024/FR-005 — the contract is live-editable on the tracker by design, and
  judging a stale contract against an amended issue is the silent-weakening
  smell. Pin the decomposition per revision, not the contract forever.
- **Cache the whole evaluator verdict per revision**: evidence must be
  re-gathered every round (the repo changes between rounds); only the rubric
  is stable. Caching verdicts would close goals on stale evidence.
- **An operator re-pin verb** (`repin_goal`, or a Decision that discards the
  pin): rejected at clarify (2026-09-05) — amending the contract text is
  already the re-pin trigger and keeps the tracker as the single truth; a
  verb that changes the rubric without changing the contract splits them.
