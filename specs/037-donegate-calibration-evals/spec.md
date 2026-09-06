# Feature Specification: Done-gate calibration eval set — measure the judge, not the vibe

**Feature Branch**: `037-donegate-calibration-evals`

**Created**: 2026-09-06

**Status**: Clarified 2026-09-06 (3 questions, all resolved) — ready for `/speckit-plan`

**Input**: User description: "Done-gate calibration eval set — a graded corpus of real done-gate rounds (contract + repo review + pinned clauses → the verdict a careful owner would give) so the judge's strictness can be measured, not guessed: first-pass convergence is 0.36 against the 0.70 ratchet and today nothing can tell a strict judge from a broken one. Fixtures for the other five prompts (admission lint, intake readiness, self-triage, review gate, browser reachability) ride the same spec."

## Why (context the requirements hang off)

The scorecard's failing metric is first-pass convergence: 0.36 of closed
goals closed on their first done proposal, against the 0.70 ratchet
(2026-09-05 reading). Spec 035 removed one cause — the rubric was
re-derived every round — and the number will move. But the metric cannot
say WHY a goal needed four rounds: a judge that correctly held a
half-finished goal open and a judge that invented a clause the contract
never carried both score 0.25. Today the only instrument for the judge's
strictness is Denys reading gate logs after a bad night (fs-479: satisfied
at 11:06, the sole failure at 12:24, no repo change between).

The evaluator prompt template has shipped five times since 2026-08-21
(ceremony drops #579, the contract-truth rewrite #789, decision resolution
#805, change classes #813, pinned clauses #824), with more rule changes
riding the Python-appended blocks, and every edit was checked by re-reading
the prompt and watching the next night. There are five
evaluator fixtures, all reconstructed by hand, and they measure the
*parser* — the canned model output round-trips through `validate()`. Zero
fixtures exist for the other five prompts. The engineering audit
(2026-09-06) named the done-gate calibration set the highest-value unbuilt
measurement; constitution VIII requires exactly this instrument before any
cognitive guardrail can be shed or tightened with evidence; constitution IX
closes instruction gaps with "one line in a skill, checked by an eval" —
there is no eval to check against.

The thing being measured is **agreement with the owner**: for a round's
real inputs — the contract, the pinned clauses, the fresh repo review, the
decisions, the prior satisfied state — the verdict a careful owner would
give. A corpus of such rounds, each graded once by the owner, turns "is the
judge too strict?" into a number with two halves: how often it holds a goal
the owner would close (false hold — the 2-hour-close class, the metric's
direct cause) and how often it closes a goal the owner would hold (false
close — ships wrong code under merge-on-close, spec 025). The two are not
symmetric and are never averaged into one score.

## Clarifications

### Session 2026-09-06

- Q: Where do the real done-gate rounds in the corpus come from — production persists only a verdict line and a prompt hash per round, never the inputs or the raw output? → A: Every done-gate round persists its assembled inputs and raw verdict (size-capped, bounded retention) so fixtures are harvested, never remembered; a new persisted surface in the state domain, shipped with a doctor check. Rejected: hand reconstruction (the review report becomes the reconstructor's recollection) and flag-gated capture (the round that goes wrong is the one nobody flagged).
- Q: What does the owner's grade on a round consist of, and is a per-clause grade required for every round? → A: Required: a verdict from the judge's own set (or `contested`) plus a one-line reason. Per-clause satisfied/unsatisfied is optional, recorded when the owner disagrees with the judge's clause reading. Grading is the owner's act, minutes per round, never delegated to a model. Rejected: per-clause on every round (triples grading, starves the corpus) and verdict-only (cannot locate a disagreement).

- Q: Does a run of the calibration corpus gate prompt changes, or is it a ratchet number that prompt-editing PRs report? → A: A ratchet number: `/eng-health` and the scorecard carry the last run's three rates, and a PR that edits a cognition prompt states its before/after rates in its `/ship` body. Informational, never a merge gate; the run is on the owner's button (it spends quota; CI has no cognition binary). Rejected: a hard gate on prompt-editing PRs (moves quota spend into CI or gets skipped under pressure) and ratchet-then-gate (a promotion condition nobody would enforce).

### Rejected alternatives (direction memory)

- **A judge-LLM grading the judge.** Rejected: the corpus exists because
  there is no trusted oracle; a second model's opinion is a second unknown.
  The owner's grade is the only ground truth this spec recognises (the
  `tests/cognition` README's "no judge-LLM before we know the shape of
  correct" holds).
- **One accuracy number.** Rejected: false hold and false close have
  different costs (a night of churn vs merged wrong code) and different
  fixes (a rubric the prompt over-reads vs evidence the prompt under-reads).
  Reported separately, always.
- **Automated pass/fail on the live run in CI.** Rejected on cost and
  hermeticity: the suite is stubbed and CI carries no cognition binary
  (the 2026-09-06 conftest guard makes spawning `claude` a test failure).
  The mechanism guards stay in CI; the live run is a button.
- **Synthetic rounds generated to fill the corpus.** Rejected: a synthetic
  round encodes the author's model of the judge, which is the thing under
  test. Only rounds that happened (harvested or reconstructed from a real
  goal) enter the corpus; the `source` field says which.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A graded done-gate corpus with a two-sided agreement report (Priority: P1)

Denys has a corpus of real done-gate rounds, each carrying the exact
inputs the judge saw and the owner's grade. He runs the calibration set
against the current evaluator prompt and reads one report: agreement with
the owner's verdict, the false-hold rate, the false-close rate, and the
per-fixture disagreements with the judge's rationale next to the owner's
reason. After editing the prompt he runs it again and sees which fixtures
moved.

**Why this priority**: it is the instrument every other decision about the
judge waits on — without it "the judge is too strict" and "the judge is
broken" are the same sentence.

**Independent Test**: with the corpus at its minimum size, run the set
twice against the same prompt and once against a deliberately broken
prompt (the pinned-clauses block removed); the two same-prompt runs agree
with each other within the flakiness bound, and the broken prompt's
false-hold rate is visibly worse.

**Acceptance Scenarios**:

1. **Given** a corpus of at least 20 graded rounds spanning every verdict
   class (achieved, off_track, needs_human) and every named failure class
   (the 2-hour-close hold, stub-disguise, ceremony-drop, decision-resolved,
   causeless flip), **When** the calibration run executes, **Then** the
   report lists per-fixture owner grade vs judge verdict, and the summary
   states agreement, false-hold rate and false-close rate as three separate
   numbers with their denominators.
2. **Given** a fixture whose judge reply is malformed (no JSON, unknown
   clause id, a causeless flip), **When** the report is produced, **Then**
   that round counts as a *mechanism failure*, listed separately, never as
   a disagreement — a broken parse is not a judgment.
3. **Given** a fixture graded `contested`, **When** the report is produced,
   **Then** it appears in the listing but in none of the three rates.
4. **Given** a corpus below the minimum size or missing a verdict class,
   **When** the run starts, **Then** it refuses with the missing classes
   named — a thin corpus reports nothing rather than a misleading number.

---

### User Story 2 - Real rounds are harvested, not remembered (Priority: P2)

Every done-gate round on the live instance leaves behind the assembled
inputs the judge saw and the raw verdict it returned, so a round that went
wrong overnight becomes a fixture the next morning by grading it — no
reconstruction from logs and memory.

**Why this priority**: the corpus is only as honest as its inputs; a
reconstructed review report is the reconstructor's recollection. This is
the difference between a corpus that grows by itself and one that costs an
hour per fixture. It is P2 because a hand-built P1 corpus already delivers
the instrument.

**Independent Test**: after one done-gate round on a stub goal, the
round's inputs and raw output can be exported as a fixture that loads in
the harness and reproduces the round's verdict through the parser.

**Acceptance Scenarios**:

1. **Given** a done-gate round completes, **When** the goal's state is
   inspected, **Then** the round's inputs and raw verdict are retrievable
   by goal id and revision, capped in size, with the cap stated when hit.
2. **Given** a captured round, **When** an operator exports it, **Then**
   the result is a fixture file the harness loads unchanged, with
   `source: production-trace` and the goal, revision and round recorded.
3. **Given** a round runs, **When** capture persists it, **Then** the goal
   costs no extra cognition and the tick spends nothing extra on an idle
   goal — capture is mechanism, never a call.
4. **Given** retention is exceeded, **When** the oldest captured rounds
   age out, **Then** a round already exported as a fixture is unaffected
   (the fixture is the durable copy; capture is the inbox).

---

### User Story 3 - The other five prompts have a starter corpus (Priority: P3)

Each of admission lint, intake readiness, self-triage, the review gate and
browser reachability has at least three graded fixtures — a clear
positive, a clear negative and the case that bit us — loaded by the same
harness, with the same mechanism guards always on and the same opt-in live
run.

**Why this priority**: the audit found five prompts with zero fixtures;
each has been edited on a hunch. Three fixtures each is the floor that
makes "checked by an eval" true for a one-line instruction change. P3
because the done-gate is where the money is.

**Independent Test**: `pytest tests/cognition/` runs the mechanism guards
for all six prompts in CI with zero quota; the opt-in live run prints
owner grade vs verdict for each.

**Acceptance Scenarios**:

1. **Given** the fixture directories for the five prompts, **When** the
   mechanism guards run, **Then** every fixture loads, renders its prompt,
   and its canned output round-trips through that prompt's own parser to
   the expected outcome.
2. **Given** the admission-lint fixtures, **When** the live run executes,
   **Then** the 2026-09-02 contracts (the four that produced avoidable
   pings) grade as the lint now handles them, and a decided contract
   reports nothing undecided.

### Edge Cases

- A fixture's contract revision digest no longer matches its pinned
  listing (a hand edit): the harness refuses the fixture with both digests
  named rather than judging a rubric that never existed.
- A round with an empty review report at the done-gate: graded and judged
  as the production path judges it (fail toward off_track), and the report
  flags it so the class stays visible.
- A fixture larger than the evaluator's prompt budget: the harness applies
  the same caps production applies, and states that it did.
- Two fixtures from the same goal and revision, different rounds: both
  kept — the second round's prior-satisfied state is part of its inputs.
- The judge returns the right verdict for a wrong reason (rationale cites a
  clause the owner did not): counts as agreement on the verdict; when the
  owner recorded a per-clause grade the report shows the clause mismatch.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A done-gate fixture MUST carry everything the judge saw for
  that round — contract text and revision, pinned clauses with prior
  satisfied state, repo review report, decisions, strictness, the goal's
  stub allowances — plus the owner's grade, the grade's reason, the source
  (harvested or reconstructed) and the originating goal/revision/round.
- **FR-002**: The owner's grade MUST carry a verdict from the same set the
  judge emits (or `contested`, which excludes the round from every rate)
  and a one-line reason; a per-clause satisfied/unsatisfied grade is
  OPTIONAL and, when present, the report MUST attribute the disagreement
  to the clause.
- **FR-003**: The calibration run MUST report agreement, false-hold rate
  (owner: achieved; judge: not) and false-close rate (owner: not achieved;
  judge: achieved) as separate numbers with denominators, plus a
  per-fixture listing of owner grade, judge verdict, judge rationale and
  owner reason.
- **FR-004**: A malformed judge reply MUST be reported as a mechanism
  failure, counted separately, and never as agreement or disagreement.
- **FR-005**: The run MUST refuse below a minimum corpus (20 done-gate
  rounds; every verdict class represented; every named failure class
  represented at least once) and name what is missing.
- **FR-006**: The live run MUST be opt-in and MUST spend no cognition
  under normal `pytest`; the mechanism guards (fixture loads, prompt
  renders, canned output round-trips through the real parser) MUST run in
  CI at zero quota, for all six prompts.
- **FR-007**: Two runs of the same corpus against the same prompt MUST
  report the number of fixtures whose verdict differed between runs (the
  flakiness bound), so a prompt change smaller than the flakiness is not
  read as a change.
- **FR-008**: The report MUST be diffable between runs: a stable per-fixture
  ordering and a machine-readable summary beside the human one, so
  `/eng-health` and the scorecard can cite the last run's three rates. The
  rates are a ratchet, never a merge gate: a PR that edits a cognition
  prompt states its before/after rates in its `/ship` body, and the run is
  started by the owner, never by CI or a schedule.
- **FR-009** (US2): every done-gate round MUST persist its
  assembled inputs and raw verdict, size-capped, keyed by goal id, contract
  revision and round; an export MUST produce a harness-loadable fixture
  with `source: production-trace`. Capture is mechanism only — zero
  additional cognition, and never on an idle tick (constitution III).
- **FR-010** (US3): each of the five other prompts MUST have at least three
  graded fixtures — clear positive, clear negative, the incident case —
  and its own parser round-trip guard.
- **FR-011**: Every fixture MUST name its source; synthetic rounds are
  refused by the loader (a fixture without a real goal behind it does not
  load).
- **FR-012**: The corpus MUST include the fs-479 rounds (the 2-hour close:
  the hold at 12:24 with no repo change since 11:06), the stub-disguise
  case, at least one ceremony-drop contract, at least one
  decision-resolved clause, and at least one round where the correct verdict
  is `needs_human` (a contract that contradicts the fenced design).

### Key Entities

- **Round fixture**: one done-gate round as the judge saw it — inputs,
  provenance (goal, revision, round, source), and the owner's grade.
- **Grade**: the owner's verdict for a round, the one-line reason, the
  date graded, `contested` when undecidable; optionally per-clause
  satisfied/unsatisfied.
- **Calibration report**: one run's per-fixture listing plus the three
  rates, the mechanism-failure count, the flakiness bound, the prompt
  digest judged, the corpus size and the date.
- **Captured round** (US2): the persisted inputs and raw verdict of a live
  round, keyed by goal id, contract revision and round; size-capped;
  retained for a bounded window; exportable to a Round fixture. A new
  persisted surface (state domain) — ships with its doctor check.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A prompt edit can be evaluated in under 15 minutes of wall
  clock from "edit saved" to "three rates and the moved fixtures on
  screen", against a corpus of at least 20 rounds.
- **SC-002**: The deliberately broken prompt (pinned block removed) is
  distinguishable from the shipped prompt by the false-hold rate on the
  first run — the instrument tells strict from broken.
- **SC-003**: Over the next three prompt-editing PRs after the corpus
  exists, each states its before/after rates; none ships a false-close
  regression.
- **SC-004**: Within 30 days of US2 landing, at least half the corpus is
  harvested (`production-trace`), not reconstructed.
- **SC-005**: All six prompts have fixtures and CI-run mechanism guards;
  `prompts_without_eval_fixtures` in `/eng-health` reads 0.
- **SC-006**: The scorecard's first-pass reading is accompanied by the
  last calibration run's false-hold rate, so a low first-pass number can
  be attributed (judge vs work) instead of guessed.

## Assumptions

- The owner is the sole grader; grading is a bounded, occasional act (a
  morning after a run), not a stage in any goal's lifecycle — the human is
  not a stage (spec 032).
- The first corpus is seeded from the five existing fixtures plus
  reconstructed rounds from fs-479, fs-431, devclaw-030 and the 2026-09-05
  night; every round after capture lands is harvested (`production-trace`),
  and reconstruction is retired once SC-004 holds.
- Quota for a calibration run is the owner's to spend; a run of 20–40
  rounds is a few dollars' worth of subscription quota and is never
  scheduled automatically.
- Captured-round retention defaults to the last 30 days or the last 50
  rounds per goal, whichever is smaller, with the per-round size cap equal
  to the evaluator's own prompt budget; the plan may tighten these, the
  fixture export is the durable copy either way.
- The judge's own strictness dial (`trust`/`strict`) is part of a
  fixture's inputs; the corpus grades against the dial the round ran under.
- Fixtures live beside the existing ones under `tests/cognition/fixtures/`
  and use the existing harness shape extended, not a second harness.
- Constitution VIII: this spec IS the instrument that principle demands
  before a cognitive guardrail is shed; nothing here is a shed candidate.
  Constitution IX: the eval is "verify thick"; it adds no Python outside
  the protocol domain except US2's capture, which is state (a persisted
  record of what the protocol carried), argued here by name.
