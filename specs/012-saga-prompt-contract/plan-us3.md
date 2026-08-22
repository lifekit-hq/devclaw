# Implementation Plan: Saga & Unit-of-Work Prompt Contract — US3

**Branch**: `feat/012-us3-expected-increment-count` | **Date**: 2026-08-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/012-saga-prompt-contract/spec.md`

**Slice**: US3 ONLY — "a work item carries its expected increment count"
(FR-010, FR-010a, FR-010b, FR-011, FR-012, FR-012a, FR-013). US1 (increment
feed-forward) is merged; US2 (saga authoring slots) is a separate arc and this
plan touches none of its surface. Written as a separate file per the owner's
instruction that `plan.md` / `tasks.md` / `spec.md` stay untouched while US2 is
in flight.

**Closes**: [#600](https://github.com/lifekit-hq/devclaw/issues/600) — "Intake
records whether an ask is READY but never how BIG it is".

## Summary

Grading today answers exactly one question — is this ask groundable enough to
attempt autonomously — and records the answer as one of two durable labels.
Nothing anywhere records how *big* the ask is, so the graded record gives the
dispatcher no basis for sizing the plan, and #600's complaint ("the same
pipeline produces different shapes for no principled reason") has no data to
stand on.

This slice adds the missing axis, and only that axis:

1. **The filer claims the count.** `file_intake` gains `expected_increments`
   (a positive integer) and `increment_basis` (why). Both are rendered into a
   new `## Expected increments` section of the issue body — the durable record.
2. **The claim is read back verbatim, never re-derived.** Grading parses the
   section out of the issue body. Two grades of an unchanged work item read the
   same number out of the same bytes, so SC-005b holds by construction rather
   than by asking a model to be consistent.
3. **Grading validates, never overwrites.** The existing readiness call — one
   `claude --print`, no new call (FR-013) — additionally reports the count it
   would assess and whether that matches the claim. The model's number is
   *never* written anywhere as the count; it feeds one boolean.
4. **Disagreement and indeterminacy are surfaced, not resolved.** A new
   `needs-sizing` label plus the mirror comment name which of the four
   surfacing conditions fired. Nothing is silently defaulted or corrected.
5. **The shape stays fixed.** The ready comment states, mechanically, that the
   work item executes as a saga (`create_goal`) whatever its count — the
   dispatcher is told, at the one place they read the verdict, that there is no
   shape decision to make (FR-012).

No new cognition call, no tick-path work, no new store, no execution-shape
branch.

## Technical Context

**Language/Version**: Python 3.11+ (`from __future__ import annotations` throughout)

**Primary Dependencies**: none new — stdlib only (regex parsing + the existing
`gh` adapter and cognition seam)

**Storage**: none. The durable record is the GitHub issue: body section (the
claim) + labels (the verdicts). No SQLite table, no migration. Consistent with
"issues = intent, SQLite = execution" (ADR 0012).

**Testing**: pytest, fully stubbed. `tests/test_intake.py` (doorway shape +
body rendering) and `tests/test_intake_readiness.py` (grade, surfacing, prompt
content, zero-token guard) — both already own the fakes this slice needs
(`FakeGh`, `CannedClaude`, `RaisingClaude`).

**Target Platform**: Linux (devclaw host; layers 1 and 3 only)

**Project Type**: single Python package (`devclaw/`). `runner/`, `engine/`,
`goal/`, `quality/`, `delivery/` are untouched.

**Performance Goals**: unchanged. Grading spends the same one cognition call it
spends today; the added work is regex parsing and at most two extra `gh` label
calls on the surfacing path.

**Constraints**: the count is the filer's claim (FR-010b); readiness and sizing
are orthogonal verdicts (see Assumptions); every failure path surfaces for a
human rather than asserting agreement.

**Scale/Scope**: 4 product files touched, 0 new modules, ~12 named regression
tests, 2 docs. Sized at ONE PR.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design.*

| Principle | Verdict | Basis |
|---|---|---|
| **I. OAuth only** | PASS — unaffected | No new spawn site. The sizing assessment rides the existing `intake_readiness` cognition caller, which already goes through the OAuth-only `llm_call` seam. |
| **II. Model-agnostic worker layer** | PASS — unaffected | Layers 1 and 3 only. No skill, no hook, no runner change; the worker never sees this data in this slice. |
| **III. Zero-token idle** | PASS — actively guarded | Zero new LLM calls anywhere: the sizing assessment is extra *fields* on the prompt/response of the call grading already makes. Nothing is added to the heartbeat tick, and the existing `test_readiness_grade_adds_no_idle_tick_cognition` / `test_readiness_recovery_is_not_wired_into_the_idle_tick` guards stay green. A named test asserts the grade spends exactly ONE cognition call with sizing enabled. |
| **IV. Single writer to state** | PASS | No devclaw state is written at all. The claim lives in the issue body (written once at filing, never rewritten — FR-010b *is* an append-nothing rule) and the verdicts live in labels, the same source-of-truth surface spec 006 established. The mirror comment is a generated view, never read back. |
| **V. Verification fails closed; done is a proposal** | PASS — reinforced | FR-012a is honoured by construction: nothing in this slice can route a work item around the done-gate, because nothing in this slice selects an execution shape. Every unreviewable sizing case (no claim, model can't assess, malformed output, evaluator crash) resolves to "a human decides", never to "agreed". |
| **VI. Loud failure over silent degradation** | PASS | Four distinct surfacing reasons, each named in the comment. An unstated claim is recorded as `unstated` in the body rather than defaulted to 1. A count supplied without a basis is rejected synchronously at the doorway. |
| **VII. Fix the class, not the instance** | PASS | The "no claim" path is ONE path serving both the intake doorway and spec 009's universal adoption of hand-written issues — a hand-written issue has no claim by construction, and gets the identical surfacing rather than a special case. Sizing is a general axis on the work item, not a devclaw-repo or a finance-sentry fix. |

**Gate result**: PASS, no violations — Complexity Tracking left empty.

**Spec boundary check**: FR-014 keeps concurrency, declared file scopes and
serial integration in spec 010 — untouched here. US2's saga authoring slots are
untouched: this plan adds no slot to any goal-authoring surface.

## Assumptions (recorded per the owner's instruction to pick the reading most
consistent with the spec's rulings and write it down)

1. **The doorway does not hard-require the claim; it records its absence
   loudly.** FR-010 says a work item MUST carry a filer-supplied count, and
   FR-011 says an unestimable extent MUST be surfaced rather than defaulted.
   Making `expected_increments` a hard-required MCP parameter would satisfy the
   first by making the second unreachable — and would break every existing
   caller, including spec 009's universal adoption path, where a hand-written
   GitHub issue has no claim by construction and never will. So: both new
   parameters are optional at the doorway, an absent claim is rendered
   `unstated` in the body (never defaulted to a number), and grading surfaces
   it as one of the four `needs-sizing` reasons. What FR-010 forbids —
   a work item whose extent is silently unknown — cannot happen.
2. **A count supplied WITHOUT a basis is rejected synchronously.** FR-010 binds
   the count to its basis ("together with the basis for that claim"), and #600
   asks for a number that "can be argued with". A bare integer cannot be argued
   with. A basis with no count is fine and means "I could not estimate, here is
   why".
3. **Readiness and sizing are orthogonal verdicts.** A size dispute does NOT
   flip `devclaw-ready` to `needs-refinement`. Conflating them would make a
   groundable ask un-dispatchable over an arithmetic disagreement, and would
   re-import into readiness exactly the size judgement the spec 006 prompt
   deliberately excludes. FR-011 requires surfacing for a human decision;
   dispatch is already human-gated (stage 2), so the label plus the comment IS
   the surface. The two labels answer two questions and neither overrides the
   other.
4. **Agreement is computed mechanically, with the model's own `agrees` boolean
   as an additional dissent signal only.** `assessed != claimed` is a
   disagreement whatever the model says about itself; `agrees: false` is also a
   disagreement even when the numbers coincide. A model cannot talk its way
   into "agreed".
5. **Downstream consumption of the count is deliberately out of slice.** The
   spec says the count "sizes the plan"; who reads it to size a plan is the
   task-graph surface US2 and spec 010 own. US3's Independent Test is about
   *recording* — grade two work items, confirm each records a count with a
   stated basis and both execute as sagas. Feeding the recorded count into the
   saga prompt is named and unsized here, not built.

## Project Structure

### Documentation (this feature)

```text
specs/012-saga-prompt-contract/
├── spec.md              # UNTOUCHED (US2 arc in flight)
├── plan.md              # US1's plan — UNTOUCHED
├── tasks.md             # US1's tasks — UNTOUCHED
├── plan-us3.md          # this file
└── tasks-us3.md         # /speckit-tasks output for this slice
```

### Source Code (repository root)

```text
devclaw/
├── intake.py                    # + expected-increment claim: validation,
│                                #   body section, parser, needs-sizing label,
│                                #   surfacing decision, comment text;
│                                #   grade_and_label returns a dict
├── intake_readiness.py          # + SizingAssessment, the claim block,
│                                #   validate() parses `increments`
├── prompts/intake-readiness.md  # + the claim input + the sizing output field
└── server/tools.py              # file_intake gains the two parameters;
                                 #   regrade_intake/grade_backlog report sizing

tests/
├── test_intake.py               # doorway: claim validation + body rendering
└── test_intake_readiness.py     # grade: preservation, disagreement,
                                 #   indeterminacy, one-call guard, prompt content

docs/
├── reference/intake-shape.md    # the Fields table + a sizing section
├── flows/autonomous-issue-pipeline.md  # the grade stage now has two axes
└── INDEX.md                     # currency tags for both
```

**Structure Decision**: layer 1 (MCP surface — the two new `file_intake`
parameters) plus layer 3 (cognition caller — the extra prompt field and its
parse) plus the intake orchestrator that owns label persistence, exactly where
spec 006 put the readiness choke point. No layer-2 or layer-4 surface is
involved: nothing here touches goal state, the heartbeat, dispatch, or the
engine.

## Phase 0 — research

- **R1. Where does a durable per-work-item fact live?** In the GitHub issue,
  not SQLite. ADR 0012 and the spec-006 label design already rule this: issues
  are intent, SQLite is execution state. The claim goes in the body (written
  once at filing), the verdicts go in labels (rewritable, source of truth), the
  comment mirrors and is never read back.
- **R2. How is SC-005b (identical count across two grades) achievable?** Only
  by never asking a model for the number that gets recorded. Parsing it out of
  the issue body makes re-grade determinism a property of byte equality. This
  is precisely the spec's 2026-08-22 ruling ("a grader-judged number … would
  drift between identical re-grades").
- **R3. Can the assessment ride the existing call?** Yes — `intake_readiness`
  already sends the full ask plus a repository snapshot and parses a JSON
  object. Adding one nested object to that schema costs no call and a few
  prompt lines. FR-013 is satisfied without any new caller module.
- **R4. What must NOT change?** The three-element readiness definition and its
  fail-closed behaviour (spec 006 FR-005). The sizing fields are additive; a
  response with no `increments` key still yields a valid readiness verdict, and
  a crash still lands `needs-refinement`.
- **R5. Does anything today branch on size?** No. `dispatch_task` vs
  `create_goal` is a human choice at stage 2, and nothing reads a size because
  none is recorded. So FR-012 needs no branch *removed* — it needs the absence
  guarded and stated where the dispatcher reads it.

## Phase 1 — design

### The claim (durable, filer-owned)

Rendered by `intake.issue_body` as a new section between `Context` and
`Provenance`:

```text
## Expected increments

- **Claimed by the filer:** 3
- **Basis:** three independent surfaces — the API handler, the store, the UI
```

`unstated` replaces the number when the filer gave none. `intake.parse_expected_increments(body)`
reads it back as `(count | None, basis, stated)`; a body with no section yields
`(None, "", False)` — the hand-written-issue case.

### The assessment (ephemeral, grader-owned)

`intake_readiness.SizingAssessment(assessed: int | None, agrees: bool | None,
basis: str)`, carried on `ReadinessVerdict.sizing`. Parsed defensively:
anything unusable yields `assessed=None, agrees=None` — which routes to "a
human decides", never to "agreed".

### The surfacing decision (pure, mechanical)

`intake.sizing_outcome(claimed, stated, assessment) -> (needs_human, reason)`,
in priority order:

| Condition | Reason recorded |
|---|---|
| the filer stated no count | `no expected increment count was stated by the filer` |
| the filer stated a basis but no count | `the filer could not estimate the extent` |
| the grader returned no assessment | `grading could not assess the extent confidently` |
| `assessed != claimed`, or `agrees is False` | `grading assessed N increment(s) against the filer's claim of M` |
| otherwise | agreement — no label |

`needs-sizing` is added when `needs_human`, and removed when not (so a re-grade
of an amended issue flips cleanly, mirroring the readiness label swap).

### The comment

The readiness mirror comment gains a sizing paragraph naming the recorded
claim, the assessment, and the reason if any — and, on the ready path, the
fixed-shape line: this work item executes as a saga via `create_goal` whatever
its expected count, and the completion judgement is never bypassed.

### Return shape

`intake.grade_and_label` returns a dict (`readiness`, `expected_increments`,
`increment_basis`, `assessed_increments`, `sizing`, `sizing_reason`) instead of
the bare label string; `regrade` merges it into its result, and `grade_backlog`
gains a `needs_sizing` bucket listing the URLs that need a human.

## Complexity Tracking

*No constitution violations — section intentionally empty.*
