# Implementation Plan: Pinned done-gate clauses — decompose the contract once per revision

**Branch**: `035-pin-donegate-clauses` | **Date**: 2026-09-05 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/035-pin-donegate-clauses/spec.md`

## Summary

The done-gate re-derives its rubric every round; this arc makes the first
round's decomposition authoritative per contract revision. The pin is
**harvested, not added**: the evaluator already emits a per-clause array
(`_parse_clauses` → `ClauseVerdict`), so round 1's parsed clauses become the
pinned list (mechanical ids `c1..cN` assigned at pin time), persisted one
record per (goal, revision digest) via the GoalStore. Later rounds render
the pinned list into the prompt and skip decomposition; verdicts must
reference clause ids; per-clause accounting (satisfied set + evidence +
flip-cause discipline) rides the same record. Zero new cognition calls,
no new verbs, no tick-path work.

## Technical Context

**Language/Version**: Python 3.11 (existing repo toolchain)

**Primary Dependencies**: none new — stdlib (`hashlib`, `json`, `sqlite3` via the existing store layer)

**Storage**: SQLite (`devclaw.db`) — one new table `goal_contract_pins`, written only through `GoalStore` (Principle IV)

**Testing**: pytest, fully stubbed (`FakeClaude`); extends `tests/test_goal_tick.py` / the done-gate test module + doctor seeded-faults in `tests/test_doctor.py`

**Target Platform**: the deployed VPS instance (layer 2/3); no worker/runner or sandbox change

**Project Type**: internal service machinery — no external interface change (MCP tool surface untouched)

**Performance Goals**: unchanged round latency; the pin read/write is one SQLite row per round

**Constraints**: zero-token idle (Principle III) — everything happens inside an already-running done-gate round; evaluator stays ONE cognition call per round (spec Assumption)

**Scale/Scope**: pins are tiny (≤ ~20 clauses × ~200 chars); history-per-revision retention is negligible

## Constitution Check

- [x] **I. OAuth only** — no new spawn site; the evaluator call path is unchanged, strip logic untouched.
- [x] **II. Model-agnostic worker layer** — layer 2/3 only; no worker skill, runner, or sandbox change.
- [x] **III. Zero-token idle** — pin read/write happens only inside a running done-gate round (FR-009); no new tick-path work, `FakeClaude.calls == 0` guards stay green.
- [x] **IV. Single writer** — the pin table is owned by `GoalStore`; writes ride the existing round-settlement path; nothing reads a view back.
- [x] **V. Fail-closed / done is a proposal** — strengthened: unknown clause id, missing flip-cause, or unparseable decomposition fails the round closed (#186); the done-gate stays always-hard; close authority unchanged.
- [x] **VI. Loud failure** — corrupt/missing pin recovery is recorded in the round rationale (FR-006); ceremony drops recorded at pin time; every flip carries a cited cause.
- [x] **VII. Fix the class** — the class is named: one definition of the contract (the task_change doctrine applied to the rubric); the fs-479 incident is the instance.
- [x] **VIII. Cognitive guardrail?** — **No guardrail is added.** The pin does no thinking the model should do — it persists the model's *own* round-1 thinking and holds later rounds to it; that is state discipline (the one-definition class: task_change #630, views-never-read-back #617), a structural invariant, not a shed candidate. The id-echo and flip-cause requirements are verdict-format contracts (like the existing JSON parse), also structural. The genuinely cognitive part — decomposition quality — is deliberately NOT mechanized here; it is measured by the done-gate calibration eval set (the spec's named companion), which is exactly ADR 0004's instrument lane.

## Project Structure

### Documentation (this feature)

```text
specs/035-pin-donegate-clauses/
├── spec.md              # clarified spec (5 clarify Qs + 4 review-round rulings)
├── plan.md              # this file
├── research.md          # Phase 0 — design decisions + rejected shapes
├── data-model.md        # Phase 1 — pin record + table schema
├── quickstart.md        # Phase 1 — validation scenarios
└── tasks.md             # /speckit-tasks output (not created here)
```

(`contracts/` omitted: no external interface changes — the MCP tool surface,
runner protocol, and prompts' JSON verdict schema are internal seams; the
verdict-shape change is documented in data-model.md.)

### Source Code (repository root)

```text
devclaw/goal/
├── clause_pin.py        # NEW — pin record model, id assignment (c1..cN),
│                        #   byte-identical carry-forward, (de)serialization
├── state.py             # +goal_contract_pins table DDL + migration
├── state_status.py      # (unchanged shape; donegate_progress keeps its home)
├── store pieces         # GoalStore: read_pin(goal_id, revision) / write_pin(...)
│                        #   / update_pin_accounting(...) — single-writer seam
├── tick_donegate.py     # pin lookup around _live_contract's digest; harvest
│                        #   round-1 clauses; pass pinned list to evaluator;
│                        #   churn-counter exemption for malformed rounds;
│                        #   re-pin + carry-forward on digest change
└── evaluator.py         # build_prompt gains pinned-clauses mode; _parse_clauses
                         #   enforces ids + flip_cause when pinned

devclaw/prompts/
└── goal-evaluator.md    # step 1/1a become conditional: decomposition mode
                         #   (no pin yet) vs pinned mode (judge exactly these ids)

devclaw/doctor/
└── checks_instance.py   # +check_contract_pins (table present, rows key to
                         #   real goals, active revision's pin parses)

tests/
├── test_goal_tick.py / done-gate module — extend named tripwire cases:
│     pin-once across rounds · unknown-id fails closed (no churn increment) ·
│     flip-without-cause malformed · re-pin carry-forward · decided clause
│     counted satisfied-with-Decision-evidence
└── test_doctor.py       # seeded faults for check_contract_pins
```

## Phase 0 → research.md

Four design decisions resolved (no NEEDS CLARIFICATION remained after the
clarify session): harvest-vs-separate-call, id scheme, accounting home,
churn-exemption mechanics. See [research.md](./research.md).

## Phase 1 → data-model.md, quickstart.md

Pin record schema, verdict-shape delta, and the stubbed validation
scenarios. See [data-model.md](./data-model.md) and
[quickstart.md](./quickstart.md).

## Slicing (unit of review; the whole spec is the commitment)

- **PR 1 (US1 + FR-008)**: the pin itself — `clause_pin.py`, table +
  migration, GoalStore seam, harvest-on-first-round, pinned-mode prompt +
  id-enforcing parse, fail-closed on unknown id, doctor check + seeded
  fault, tripwire test "one decomposition per revision".
- **PR 2 (US2 + FR-011)**: monotonic accounting — satisfied-set persistence,
  stable-denominator `donegate_progress`, refusal-names-pinned-ids, flip
  rule (prompt + parse + malformed-round handling), churn-counter exemption,
  tripwire test "satisfied clauses cannot vanish; flips carry cause".
- **PR 3 (US3 + FR-007)**: amendment lane — re-pin on digest change (once,
  named in rationale), byte-identical carry-forward, Decisions survive
  re-pin, tripwire case extension.

Post-merge: deploy is Denys's button (spec 005 FR-008); the pin activates on
the next done-gate round per goal with no migration (spec Edge Case).
