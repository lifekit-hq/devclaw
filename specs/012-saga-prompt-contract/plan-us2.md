# Implementation Plan: Saga & Unit-of-Work Prompt Contract — US2

**Branch**: `feat/012-us2-saga-authoring-slots` | **Date**: 2026-08-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/012-saga-prompt-contract/spec.md`

**Slice**: US2 ONLY — "a saga is authored against a schema, not prose"
(FR-007, FR-008, FR-009, FR-009a/b as they bear on the saga framing, FR-013,
FR-014). US1 (increment feed-forward) is merged; US3 (expected increment
count) is a separate arc on the intake/grading surface and is NOT touched here.

**Process note**: `/speckit-clarify` was deliberately skipped for this slice —
the owner is not in the loop for it. Every ambiguity is resolved below under
*Assumptions taken without clarify*, each one picked as the reading most
consistent with the spec's own recorded rulings.

## Summary

Today a saga's framing is two free-prose strings — `objective` and `done_when` —
re-sent verbatim in every increment's brief. Three of the five things FR-007
names have nowhere to live: what is deliberately excluded, which invariants
must survive, and what is already established. Authors put them in prose or
leave them out, so two authors describing the same work produce different
sagas, and a worker discovers the gap mid-run.

This slice gives the saga **five named slots**, rejects a creation that leaves
a new slot unfilled (naming it), and renders the framing from a **single
generator with a fixed structure and a hard size bound** — which is what makes
FR-009a's "re-send in full with every increment" affordable (FR-009b).

Backward compatibility is structural rather than best-effort: the three new
slots are `None` when the key is ABSENT from `goal.yaml` and a list (possibly
empty) when it is present. A goal authored before the schema therefore renders
a **byte-identical** brief, and "the author declared nothing excluded" stays
distinguishable from "this goal predates the slot".

## Technical Context

**Language/Version**: Python 3.11+ (`from __future__ import annotations`)

**Primary Dependencies**: none new — stdlib + the existing `yaml` persistence

**Storage**: three new optional keys in the existing `goal.yaml` facts file
(`out_of_scope`, `invariants`, `established`). No `devclaw.db` schema change,
no migration, no backfill — absence is meaningful and is preserved.

**Testing**: pytest, fully stubbed. New `tests/test_saga_framing.py` (renderer
+ bound + legacy shape); admission rejection extends
`tests/test_goal_admission.py`; brief composition extends
`tests/test_goal_tick.py`; the grill pass-through extends
`tests/test_elicitation.py`.

**Target Platform**: Linux host, layers 1–2 (`server/tools.py`,
`goal/service.py`, `goal/admission.py`, `goal/store`, `goal/tick.py`) plus one
layer-3 prompt (`prompts/scope-grill.md`). Layers 4–5 untouched.

**Performance Goals**: pure string work on the already-gated dispatch path;
zero added I/O, zero added subprocesses, zero added LLM calls anywhere.

**Constraints**: every slot bounded at `SAGA_SLOT_KEEP = 1_200` chars, so the
whole framing is bounded by construction (`SAGA_FRAMING_MAX`, asserted by a
test against adversarial input) — FR-009b.

**Scale/Scope**: ~9 files touched, 1 new module, ~10 named regression tests.
Sized at ONE PR.

## The slot schema — and the reader that ACTS on each slot

FR-009 is the sharpest constraint in this story: *"A slot that does not change
what a worker does MUST NOT be added."* The bar taken here is literal — the
reader must be the worker, and the acting must be observable in the brief.

| Slot | Field | Required | Reader that ACTS on it |
|---|---|---|---|
| what is being achieved | `objective` (existing) | yes — already `missing_objective` | the worker: the brief's `Goal:` line, the one statement of what to pursue. Also the done-gate evaluator and every display surface. |
| what completion means | `done_when` (existing) | yes — already `missing_done_when_and_no_spec` | the done-gate evaluator decomposes it into the per-clause contract that decides whether the saga closes. Also the worker: the brief's `Done when:` line. |
| what is excluded | `out_of_scope` (**new**, `list[str]`) | yes — new `missing_out_of_scope` | the worker: a named section it must not build into. Today the only place an exclusion can live is prose the worker must infer from — the observed sprawl mode. |
| which invariants must survive | `invariants` (**new**, `list[str]`) | yes — new `missing_invariants` | the worker: a named section stating what must still hold after the increment. A change that breaks one is not shippable, which changes what it writes and what it verifies. |
| what is already established | `established` (**new**, `list[str]`) | yes — new `missing_established` | the worker: a named section of settled decisions it must not re-derive or re-litigate. This is the *static* sibling of US1's dynamic feed-forward: US1 says what previous increments SHIPPED, this says what was decided before any increment ran. |

Slots deliberately NOT added, and why:

- **an expected increment count** — that is US3, and it belongs to the work
  item, not the saga.
- **a stack / architecture slot** — the repo is the ground truth and the worker
  reads it; a restated stack is a stale second source, not a behaviour change.
- **a milestones slot** — the task graph (`specs/NNN-*/tasks.md`) already owns
  ordering, and the worker authors it. A duplicate in the framing would
  compete with it.
- **a risks slot** — nothing acts on it. The grill still elicits risks into the
  prose `spec`, which the done-gate reads; it is not re-sent per increment.
- **a unit-of-work authoring schema** — explicitly ruled out by the spec's
  Assumptions ("a unit of work gets a content contract, not an authoring
  schema"): nobody authors a unit of work.

### An empty slot is FILLED, not missing

A slot may be declared empty (`[]`); it may not be left unfilled (`None`). The
distinction is the whole point of the schema — silence cannot be told apart
from an author who forgot — and it mirrors FR-004's already-ruled doctrine that
an absence is STATED, never omitted. A declared-empty slot renders one short
explicit line ("Out of scope: nothing is excluded."), so SC-003's "same
structure, differing only in content" holds between an author who excludes
things and one who excludes nothing.

## Backward compatibility — how prose-authored goals keep working

Live goals on the VPS were authored before this schema. Three distinct
surfaces, three answers:

1. **Loading and ticking an existing goal.** `load_goal` reads
   `raw.get("out_of_scope")` — an ABSENT key yields `None`, not `[]`. The
   framing renderer emits the three new sections only when the slot is not
   `None`, so a pre-schema goal produces a **byte-identical** brief. Named
   regression test:
   `test_goal_authored_before_the_slot_schema_renders_todays_framing_byte_identical`.
2. **Creating a new goal through the MCP surface.** The three slots become
   required: an omitted slot is a structured `GoalAdmissionRejected`
   naming it, which the tool boundary already surfaces as JSON conditions the
   waiter can route on. This is the specified FR-008 behaviour, and the waiter
   is an agent that reads the rejection and re-files.
3. **Programmatic creators.** Two exist. The unattended one — the self-fix
   pickup (`goal/self_issue.py`) — gets REAL slot content (do not sprawl beyond
   the issue; keep the suite and the repo's documented invariants; the issue's
   diagnosis is accepted, do not re-triage). The deprecated operator-present
   alias `start_program` passes explicitly-empty slots, which is the FR-012b
   class: an operator is present and can correct a bad prompt immediately, so a
   required-slot tax buys nothing there.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design.*

| Principle | Verdict | Basis |
|---|---|---|
| **I. OAuth only** | PASS — unaffected | No new spawn site and no new cognition caller. The one prompt touched (`scope-grill.md`) rides an existing caller. |
| **II. Model-agnostic worker layer** | PASS | The slots render as plain imperative English inside the existing brief. No frontmatter, no vendor tool-wiring, no second home for worker instructions. |
| **III. Zero-token idle** | PASS — actively guarded | The framing is composed inside `_advance_brief`, which runs only on the already-gated dispatch path. No store read, no subprocess, no LLM call is added anywhere; slot content rides on the `Goal` object the tick already loaded. The `FakeClaude.calls == 0` guards stay green untouched. |
| **IV. Single writer to state** | PASS | Slots are goal FACTS written once at creation by `GoalStore.create_goal`, the existing writer. No new writer, no transition, no mutation after creation — goals stay durable and there is deliberately no `update_goal` (cancel + recreate remains the verb). |
| **V. Verification fails closed; done is a proposal** | PASS — unaffected | No gate is added, removed, or consulted differently. `done_when` keeps its exact current meaning and remains the done-gate's contract. |
| **VI. Loud failure over silent degradation** | PASS | An unfilled slot is a structured rejection naming the slot, at creation time — not a silent default (FR-008 / SC-004). Slot truncation announces itself with a marker naming where the full text lives. |
| **VII. Fix the class, not the instance** | PASS | Reuses `prompt_budget` for the bound rather than re-deriving one, and the shared-marker contract in `advance_brief.py` for the `Goal:` prefix that a detector already keys off (#547/#550). The change is to the authoring RULE, not to any one goal. |

**Gate result**: PASS, no violations — Complexity Tracking left empty. No
constitution amendment is required: nothing here changes an invariant.

**Spec boundary check**: FR-014 keeps concurrency, declared file scopes and
serial integration in spec 010 — untouched. US3's surface
(`devclaw/intake_readiness.py`, `regrade_intake`, `grade_backlog`) is not
touched by any file in this plan.

## Assumptions taken without clarify

1. **All five FR-007 slots are required; an empty declaration satisfies the
   requirement.** FR-008 says "a required slot", without listing which. Making
   only some required would leave exactly the silence the story exists to
   remove. Reading chosen: required-to-FILL, allowed-to-be-EMPTY.
2. **`done_when` keeps its existing "or a `spec`" escape.** Admission today
   accepts a goal with no `done_when` when a `spec` carries the acceptance
   criteria. Tightening that is not required by FR-007 (the slot IS filled,
   by a named alternative field) and would break the deprecated
   `start_program` alias, which files its brief as the spec. Left as-is.
3. **Slots are `list[str]`, not prose.** FR-007 says "named slots"; a list
   forces one atomic statement per item, which is what makes two authors'
   sagas comparable (SC-003) and what makes a per-slot bound meaningful.
   Mirrors the existing `backlog` / `stub_acceptable` fields.
4. **The done-gate's read-only review instruction
   (`tick_donegate`) is NOT given the new slots.** FR-009a and FR-009b govern
   the saga framing re-sent with every UNIT OF WORK. The done-gate review is
   one dispatch per gate round, judged against `done_when`, and FR-009's bar
   ("changes what a worker does") is not met by restating exclusions to a
   read-only reviewer. Named as a deliberate omission rather than an oversight.
5. **The scope grill emits the slots.** The grill is the authoring instrument
   the waiter uses before `create_goal`; a schema with no way to produce it is
   a hurdle rather than a contract. Its prompt already elicits "scope
   (explicitly in AND out)" and "hard constraints" — they simply landed in
   prose. The three lists become OPTIONAL keys on the existing `done` action,
   so an older caller and every existing test are byte-unaffected.

## Project Structure

### Documentation (this feature)

```text
specs/012-saga-prompt-contract/
├── spec.md              # UNTOUCHED by this slice (owned by the owner + US3)
├── plan.md / tasks.md   # UNTOUCHED — US1's
├── plan-us2.md          # this file
└── tasks-us2.md         # this slice's task list
```

### Source Code (repository root)

```text
devclaw/
├── advance_brief.py          # + GOAL_LINE_PREFIX (the two-sided `Goal:` detector)
├── elicitation.py            # validate_step passes the three slot lists through
├── prompts/scope-grill.md    # finalize emits the slots alongside the spec
├── goal/
│   ├── saga_framing.py       # NEW — pure renderer, fixed structure, bounded
│   ├── prompt_budget.py      # + SAGA_SLOT_KEEP + cap_saga_slot (head-keep)
│   ├── admission.py          # + missing_out_of_scope/_invariants/_established
│   ├── models.py             # Goal gains the three Optional[list[str]] slots
│   ├── service.py            # create_goal / verify_goal thread + get_goal shows
│   ├── self_issue.py         # self-fix pickup fills real slots
│   ├── tick.py               # _advance_brief delegates framing to saga_framing
│   └── store/base.py         # goal.yaml write + absence-preserving read
└── server/tools.py           # create_goal / verify_goal params; start_program

tests/
├── test_saga_framing.py      # NEW — structure, empty-slot, legacy, bound
├── test_goal_admission.py    # + the three rejections, named
├── test_goal_tick.py         # + brief composition + legacy byte-identity
└── test_elicitation.py       # + grill slot pass-through
```

**Structure Decision**: Layer 1 (the MCP authoring surface) + layer 2 (goal
facts, admission, brief composition), per CLAUDE.md's layer map. The renderer
is a pure layer-2 helper with no I/O, sitting beside `prior_increments.py`
which it structurally mirrors. Layer 3 is touched only by a prompt edit on an
existing caller; layers 4–5 are untouched.

## Complexity Tracking

*No constitution violations — section intentionally empty.*
