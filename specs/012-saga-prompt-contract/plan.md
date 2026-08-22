# Implementation Plan: Saga & Unit-of-Work Prompt Contract — US1

**Branch**: `feat/012-saga-prompt-contract` | **Date**: 2026-08-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/012-saga-prompt-contract/spec.md`

**Slice**: US1 ONLY (increment feed-forward, the spec's P1). US2 (saga
authoring slots) and US3 (expected increment count) stay named-unsized per the
spec's own priority rules and the constitution's slice-don't-estimate rule.

## Summary

An increment of a saga must know what its siblings delivered. Today every
advance session receives a brief carrying the goal's objective and `done_when`
but nothing about what previous increments in the same goal actually shipped —
so work has been re-implemented that was already delivered.

The delivery outcomes are already recorded: `tick_settle` writes a
`goal_deliveries` row on every settle (done, failed, gate-FAILED), carrying the
increment's objective and devclaw's controlled settle header (status, sandbox
gate verdict, PR url). Nothing reads them back into the worker's prompt.

This slice feeds them forward: a new pure renderer turns those rows into a
compact, size-bounded prompt section; `_advance_brief` gains it as a blank-safe
marked section; `_handle_long_lived_advance` composes it after the
`should_plan` gate so idle ticks are byte-identical to today. No new storage,
no new reasoning call, no change to the worker return contract.

## Technical Context

**Language/Version**: Python 3.11+ (`from __future__ import annotations` throughout)

**Primary Dependencies**: none new — stdlib only (the renderer is pure text)

**Storage**: existing `goal_deliveries` rows in `devclaw.db`; read-only in this
slice. No schema change, no migration.

**Testing**: pytest, fully stubbed (`tests/goal_fakes.py`: `FakeClaude`,
`FakeEngine`, `RecordingNotifier`, `seed_goal`). New module gets
`tests/test_prior_increments.py`; tick-path behavior extends
`tests/test_goal_tick.py`.

**Target Platform**: Linux (devclaw host; layers 2–4 — the sandbox is untouched)

**Project Type**: single Python package (`devclaw/`) + in-sandbox runner
(`runner/`, NOT touched by this slice)

**Performance Goals**: composition is one SQLite read + pure string work on the
work-present dispatch path only; idle/blocked ticks unchanged (zero added I/O)

**Constraints**: the assembled section is tail-kept at 6 000 chars (FR-009b);
re-sent in full on every increment (FR-009a); no LLM call anywhere in the path
(FR-013)

**Scale/Scope**: ~5 files touched, 1 new module, 1 new store accessor, ~7 named
regression tests. Sized at ONE PR.

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design.*

| Principle | Verdict | Basis |
|---|---|---|
| **I. OAuth only** | PASS — unaffected | No new spawn site, no new cognition caller; nothing touches the key-stripping seams. |
| **II. Model-agnostic worker layer** | PASS | The section is plain imperative English in the existing brief; no vendor tool-wiring, no `Skill(...)`, no frontmatter. The runner's agent-drive seam is untouched. |
| **III. Zero-token idle** | PASS — actively guarded | The delivery read + render happen strictly AFTER the `should_plan` gate (research R7). Idle and blocked tick paths are byte-identical; a named test asserts no delivery read on the idle path, and the existing `FakeClaude.calls == 0` guards stay green. |
| **IV. Single writer to state** | PASS | Read-only path. Rows are the source of truth; `deliveries.md` (a generated view) is never read back. No new writer, no transition. |
| **V. Verification fails closed; done is a proposal** | PASS — unaffected | No gate is added, removed, or consulted differently. The done-gate's `recent_deliveries` input is untouched. The section explicitly tells the worker NOT to treat failed/gate-FAILED increments as shipped, which reinforces rather than relaxes the trust boundary (#358). |
| **VI. Loud failure over silent degradation** | PASS | Truncation announces itself (marker names the elision + where the full record lives); an unreadable delivery block renders a stated gap rather than being silently dropped; the renderer never raises into the dispatch path. |
| **VII. Fix the class, not the instance** | PASS | Reuses the shared `prompt_budget.cap_section` rather than re-deriving a bound (the #422/#431 class fix), and the shared marker-constant contract in `advance_brief.py` rather than a local literal (#547/#550). |

**Gate result**: PASS, no violations — Complexity Tracking left empty.

**Spec boundary check**: FR-014 keeps concurrency, declared file scopes, and
serial integration in spec 010. This plan touches none of them.

## Project Structure

### Documentation (this feature)

```text
specs/012-saga-prompt-contract/
├── spec.md              # merged 2026-08-22 (+ #607 boundary fixes)
├── plan.md              # this file
├── research.md          # Phase 0 — R1..R7
├── data-model.md        # Phase 1 — entities + new surfaces
├── quickstart.md        # Phase 1 — validation guide
├── contracts/
│   └── prior-increments-section.md   # the prompt-section contract
├── checklists/
└── tasks.md             # /speckit-tasks output (not created here)
```

### Source Code (repository root)

```text
devclaw/
├── advance_brief.py          # + PRIOR_INCREMENTS_MARKER; display_goal annotates
├── goal/
│   ├── prior_increments.py   # NEW — pure renderer, never raises
│   ├── prompt_budget.py      # + PRIOR_INCREMENTS_KEEP + truncation marker
│   ├── tick.py               # _advance_brief gains blank-safe kwarg;
│   │                         #   _handle_long_lived_advance composes post-gate
│   └── store/
│       └── content.py        # + delivery_blocks() read accessor
└── (runner/, engine/, quality/, delivery/ — UNTOUCHED)

tests/
├── test_prior_increments.py  # NEW — renderer unit + bound + degradation
└── test_goal_tick.py         # + brief-composition and idle-path regressions

docs/
├── flows/task-execution.md   # brief composition gains a section — check & fix
└── INDEX.md                  # currency tag if a doc changes
```

**Structure Decision**: Layer 2 (GoalService/heartbeat) + a thin layer-2 helper
module, per CLAUDE.md's layer map: this is goal-state-derived prompt
composition, not protocol (layer 1), not a cognition caller (layer 3 — there is
no LLM call), not dispatch/engine (layer 4), not the worker harness (layer 5).
The store accessor is read-only projection over existing rows.

## Complexity Tracking

*No constitution violations — section intentionally empty.*
