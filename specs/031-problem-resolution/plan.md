# Implementation Plan: Structured problem resolution

**Branch**: `031-problem-resolution` | **Date**: 2026-09-02 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/031-problem-resolution/spec.md`

## Summary

A goal that cannot proceed for a human-gated reason carries a typed
**Problem** (what, clause, why-undecidable, options, default, timebox) instead
of prose in `blocked_on`. The owner resolves it with exactly two verbs —
**correct_implementation** and **decide** — each recording a **Decision** that
unblocks the goal through the existing CAS'd transition and never touches the
steering inbox. A timed-out Problem takes its default and informs. Defective
`done_when` contracts are refused or rewritten at creation. Decisions ride the
prior-increments feed-forward channel so the worker and the done-gate read them
as settled fact.

Technical approach: two append-only tables (`goal_problems`,
`goal_decisions`) plus one pointer column (`goal_status.problem_id`); no new
state machine state or event — a Problem lives alongside `phase="blocked"` and
resolution is `Event.UNBLOCK` with the same budget-restoring shape as
`steer_goal`; the timebox is a cheap timestamp compare on the blocked branch
of the tick (zero-token); a defaulted *accept and close* never closes directly
— it marks the clause resolved and lets the done-gate's single ACHIEVE emitter
close on its next round.

## Technical Context

**Language/Version**: Python 3.11+ (runtime as pinned by `pyproject.toml`; mypy `python_version = "3.11"`)

**Primary Dependencies**: stdlib `sqlite3` (`GoalStore` / `state_*.py`), FastMCP (`@mcp.tool`, `@mcp.custom_route`), the existing `load_prompt` template layer

**Storage**: SQLite `devclaw.db` — two new tables + one `goal_status` column, migrated by the idempotent `ALTER`/`CREATE IF NOT EXISTS` at `GoalState` construction (same mechanism as `donegate_progress`, #802)

**Testing**: pytest, fully stubbed (`tests/goal_fakes.py`: `FakeClaude`, `FakeEngine`, `RecordingNotifier`, `seed_goal`); tripwire classes only — CAS/single-writer, zero-token idle, fail-closed, doctor seeded-faults, structural guards

**Target Platform**: the deployed `devclaw-mcp` container on `lifekit-vps` (Linux/ARM64), driven over MCP by the OpenClaw waiter and over HTTP by the console

**Project Type**: long-running service (MCP + HTTP) with a heartbeat loop

**Performance Goals**: no measurable change to tick cost — the new per-tick work is one indexed row read on blocked goals only

**Constraints**: zero-token idle (no LLM call on idle/blocked ticks; the admission lint's one cognition call runs at creation, never on the tick); single writer through `GoalStore.transition` + `LEGAL`; exactly one `Event.ACHIEVE` emitter (`tick_donegate.py`); fail-closed gates untouched; persisted-shape change ships a doctor check + seeded-fault tests; skills stay plain markdown

**Scale/Scope**: ~10 active goals per project, tens of Problems per goal-month; the feed-forward section is budget-capped like prior increments

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | How the design honours it |
|---|---|---|
| I. OAuth only | ✅ untouched | no new spawn site; the lint's cognition call goes through the existing `cognition.py` caller |
| II. Model-agnostic worker layer | ✅ | the Decisions section is a plain-text brief section rendered host-side; no skill change beyond one markdown line telling the worker Decisions are settled fact |
| III. Zero-token idle | ✅ | the timebox check is a timestamp compare on `goal_problems.timebox_at`, placed on the blocked branch before the `should_plan` gate; `FakeClaude.calls == 0` on an idle/blocked tick with an open Problem is a named tripwire (SC-006) |
| IV. Single writer to state | ✅ | Problems/Decisions are written only by `GoalStore` methods inside the same transactions that raise/clear the block; every unblock is `Event.UNBLOCK` via `transition(expect=)`; the MCP verbs call `GoalService`, never the store directly |
| V. Verification fails closed; done is a proposal | ✅ | no new ACHIEVE emitter: a defaulted *accept and close* records the Decision and returns the goal to idle; the done-gate's next round grades the clause *resolved by decision* and closes through the existing path — under `strict` the timeout parks instead (Q2). No gate's consequence changes |
| VI. Loud failure | ✅ | a Problem the loop cannot populate falls back to the fixed option set and says so; a lint miss surfaced by a worker-block is recorded in the problems catalog; a Problem row that cannot be read blocks legibly |
| VII. Fix the class | ✅ | every human-gated raise site (four today) goes through ONE `raise_problem` seam; the admission lint is class-based (three classes), not a list of today's four incidents |
| Workflow: doctor for persisted shape | ✅ | `instance.problems.tables` + `instance.problems.status_pointer` checks with seeded-fault pairs |
| Workflow: tripwire tests only | ✅ | tests listed in quickstart are all tripwire-class; ordinary rendering/wording ships no test |

**Gate result (pre-research)**: PASS — no violations, nothing to justify in Complexity Tracking.

**Gate result (post-design, re-evaluated after Phase 1)**: PASS — the data model added no second writer and the contracts added no new state or event; the one cognition call is creation-time only.

## Project Structure

### Documentation (this feature)

```text
specs/031-problem-resolution/
├── plan.md              # This file
├── research.md          # Phase 0 — every design decision, with alternatives
├── data-model.md        # Phase 1 — Problem, Option, Decision, the status pointer
├── quickstart.md        # Phase 1 — runnable validation scenarios
├── contracts/
│   ├── mcp-and-http.md  # the two verbs, the refusal, the read shape, routes
│   ├── brief-section.md # the Decisions feed-forward section
│   └── evaluator.md     # done-gate input/output changes
└── tasks.md             # Phase 2 (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
devclaw/
├── goal/
│   ├── models.py            # Problem, ProblemOption, Decision dataclasses; GoalStatus.problem_id
│   ├── state.py             # CREATE goal_problems / goal_decisions; ALTER goal_status ADD problem_id
│   ├── state_status.py      # persist/read problem_id (four sites, as donegate_progress)
│   ├── state_problems.py    # NEW — row I/O for the two tables (append-only; current-problem query)
│   ├── store/content.py     # raise_problem / resolve_problem / current_problem / decisions_for(goal)
│   ├── problems.py          # NEW — the ONE raise seam + option-set builders + timebox default
│   ├── decisions.py         # NEW — feed-forward render (mirrors prior_increments.py)
│   ├── admission_lint.py    # NEW — the three contract classes; pure for (a)/(b), cognition for (c)
│   ├── tick.py              # blocked branch: timebox default before should_plan; dispatch reads decisions
│   ├── tick_donegate.py     # needs_human + churn park raise Problems; clause-with-Decision grading
│   ├── tick_settle.py       # worker honest-block raises a Problem (fail fast, no cap burn)
│   ├── service.py           # resolve_problem(); steer_goal refuses when a Problem is open; create_goal runs the lint
│   ├── evaluator.py         # decisions kwarg (blank-safe); parse `resolved_by_decision` clause verdicts
│   └── prior_increments.py  # unchanged; decisions.py is its sibling
├── advance_brief.py         # DECISIONS_MARKER; the section slot
├── prompts/goal-evaluator.md# grade a clause carrying a Decision as resolved by it
├── server/
│   ├── tools/goals.py       # correct_implementation, decide (MCP); get_goal/list_goals carry `problem`
│   └── routes/goals.py      # POST /goals/{id}/resolve; goal_json carries `problem` + `decisions`
├── doctor/checks_instance.py# instance.problems.tables, instance.problems.status_pointer
└── notify wording           # owner ping names clause + options + the two verbs
runner/skills/_writes-code/  # one line: Decisions in the brief are settled — do not re-derive
tests/
├── test_goal_tick.py        # tripwires: zero-token with open Problem; timebox default; refuse steer
├── test_goal_transitions.py # UNBLOCK-by-resolution rides LEGAL unchanged
├── test_doctor.py           # seeded-fault pairs
└── test_harness_docs_map.py # (unchanged; docs listed in INDEX)
```

**Structure Decision**: single package, existing layer map. Layer 1 (`server/`) gains two tools and one route and calls `GoalService`; layer 2 (`goal/`) owns the state, the raise seam, the resolution, the lint, and the feed-forward; layer 3 (`evaluator.py` + prompt) gains one blank-safe input and one clause-verdict kind; layers 4–5 are untouched except one markdown line in the worker skill.

## Complexity Tracking

> No constitution violations — nothing to justify.

## Increments (unit of review, not of commitment — the whole spec is the commitment)

| increment | stories | ships as |
|---|---|---|
| **P1** | US1 + US2 | tables + pointer + raise seam at all four sites + the two verbs + timebox default + steer refusal + console/ping + doctor checks |
| **P2a** | US4 | `decisions.py` render + brief slot + evaluator input + prompt rule + skill line |
| **P2b** | US3 | `admission_lint.py` + `create_goal` hook + refusal contract |

P2a before P2b: a Decision that is not fed forward is a row nobody reads, and
the lint's class-(b) rewrites are themselves Decisions that must reach the gate.
