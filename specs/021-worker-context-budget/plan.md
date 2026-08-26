# Implementation Plan: Worker Context-Budget Invariant

**Branch**: `021-worker-context-budget` | **Date**: 2026-08-26 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/021-worker-context-budget/spec.md`

## Summary

Kill the "Prompt is too long" wedge class (devclaw#707) by decoupling worker-session
input size from both the ask and the repo. Three increments: (US1) harness-enforced
one-chunk-per-session where a **chunk = the speckit story-slice the worker already
plans** — the runner watches `specs/*/tasks.md` slice completion and ends the turn
mechanically; continuation dispatches ride the existing capped brief (prior
increments ≤6k chars) so input never grows with the arc. (US2) a runner-side
context tripwire on the agent's `usage_update` stream (used/size — emitted by the
production agent today, currently unparsed) that cancels the turn at a threshold
and sends a "land what's coherent" follow-up prompt in the same session; firings
are recorded as events + a problems-catalog row. (US3) read-side diet via the
worker skill: per-slice distilled context recorded in the feature's `plan.md` and
build sessions instructed to pull from it + the repo brief before raw exploration.

No new artifact format is invented: the chunk plan **is** the speckit
`spec.md`/`plan.md`/`tasks.md` the worker already commits; chunk done-ness is
derived from host settle records (`goal_deliveries`/increment_records), exactly as
clarified in the spec (Session 2026-08-26).

## Technical Context

**Language/Version**: Python 3.11+ (host `devclaw/`, tests); the in-sandbox
`runner/runner.py` + `runner/acp_client.py` are ZERO-DEPENDENCY stdlib-only
modules baked into the sandbox image — any runner change must stay stdlib-only.

**Primary Dependencies**: none added. Host: FastMCP/SQLite as today. Runner: stdlib.

**Storage**: existing SQLite (`devclaw.db`): `goal_deliveries`, `events`,
`problems` tables — no schema change expected; chunk done-ness is derived from
existing settle records. Workspace artifacts: `specs/NNN-*/{spec,plan,tasks}.md`
on the goal branch (already ridden by `task_change.materialize_worktree_sync`).

**Testing**: pytest, fully stubbed (`-n auto`, ~2500 tests). Runner behavior via
the spec-011 fake-agent harness (`tests/acp_fake_agent.py` scripts +
`tests/test_runner_acp.py` subprocess driver). Brief/dispatch behavior via
`tests/goal_fakes.py` (`FakeEngine.dispatched` carries the assembled brief).

**Target Platform**: Linux host + per-task docker sandbox (prod); `host`/`stub`
engine modes for dev/CI.

**Project Type**: existing single Python project + standalone runner (layer 5).

**Performance Goals**: tripwire adds no measurable per-update cost (string/dict
inspection on notifications already pumped); slice-watcher reads `tasks.md` only
at tool-call-update boundaries (bounded file reads inside the sandbox).

**Constraints**: model-agnostic worker seam (plain-markdown skills, ACP-level
messages only — `usage_update` parsing stays tolerant/optional, FR-007 inert-loud
when absent); zero-token idle untouched (no tick-path cognition added); runner
stays zero-dep; skill-brief size ceilings in `tests/test_runner_skills.py`
(<13000 / <12600 chars) bumped deliberately if skill text grows.

**Scale/Scope**: ~6 host modules touched, 2 runner modules, 1–2 skill files,
~10 named regression tests. No persisted-state shape change anticipated ⇒ no new
doctor check unless the tasks phase discovers one (spec 016 FR-014 re-checked then).

## Constitution Check

*GATE: evaluated pre-research and re-checked post-design — PASS on all seven.*

| Principle | Verdict | Note |
|---|---|---|
| I. OAuth only | PASS | No new spawn sites; runner/env changes forward only `DEVCLAW_*` knobs (existing acp_env allowlist pattern, runner.py ~L1396–1425). |
| II. Model-agnostic worker layer | PASS | Enforcement reads workspace files + ACP `session/cancel` + a follow-up `session/prompt`; skills stay plain markdown in `runner/skills/` (one home); `usage_update` handling is a tolerant scavenger extension, not vendor wiring — an agent that emits nothing leaves the tripwire inert-and-loud (FR-007). |
| III. Zero-token idle | PASS | All new behavior lives inside dispatched sessions / settle; nothing added to the tick idle path. `FakeClaude.calls == 0` guards stay green. |
| IV. Single writer to state | PASS | Runner READS `tasks.md`, never writes it (worker stays sole writer); chunk done-ness derived from existing settle rows — no new writer, no view read-back. |
| V. Verification fails closed; done is a proposal | PASS | Tripwire-landed sessions ride the standard verify/settle path unchanged; the `cancelled`-stopReason-flows-as-ok hole is FIXED (a cancelled turn without the land-now follow-up is not an ok result); missing/corrupt `tasks.md` on continuation blocks loud (FR-004). |
| VI. Loud failure over silent degradation | PASS | Inert tripwire says so once in the task record; oversized-chunk marking feeds re-slice, identical retries refused (FR-008); bounded coverage named. |
| VII. Fix the class, not the instance | PASS | This spec is the class fix for #707; slice enforcement also mechanizes the slice-guard's build-ahead rule (same class, second instance). |

## Project Structure

### Documentation (this feature)

```text
specs/021-worker-context-budget/
├── spec.md              # clarified spec (Session 2026-08-26)
├── plan.md              # this file
├── research.md          # Phase 0 — decisions grounded in code reading
├── data-model.md        # Phase 1 — entities mapped to existing tables/artifacts
├── quickstart.md        # Phase 1 — validation guide
├── contracts/           # Phase 1 — runner result/event lines, env knobs, chunk grammar
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
runner/
├── acp_client.py        # parse usage_update (used/size) → context ratio; cancel+follow-up prompt support; fix cancelled→ok hole
├── runner.py            # slice-completion watcher (tasks.md flips vs session start); tripwire orchestration; ContextTripwire event + result markers; env knobs
└── skills/_writes-code/05-speckit-memory.md   # chunk contract text: one slice per session (now mechanically enforced), per-slice distilled context in plan.md, pull-from-brief-first

devclaw/
├── goal/tick.py                 # _advance_brief: continuation framing (chunk done-ness from settle records; oversized-chunk failure context)
├── goal/tick_settle.py          # settle → chunk progress derivation (existing increment_records path; no schema change)
├── goal/slice_guard.py          # reuse checkbox/slice parsing shape (reference for runner-side parser; host verdict interplay documented)
├── queue/settle.py              # tripwire result-marker → record_problem; oversized-chunk marking in failure detail; no-identical-retry guard
├── config.py                    # DEVCLAW_CONTEXT_TRIPWIRE_PCT accessor (default 75; 0 disables) + forwarding declaration
└── server/worker_events.py      # decode ContextTripwire event for the console (generic fallback exists; add a friendly case)

tests/
├── acp_fake_agent.py            # new scripts: usage_window (used/size stream), slice_flip (writes tasks.md flips), overrun (keeps working past flip)
├── test_runner_acp.py           # subprocess-level: tripwire fires once, cancel+land-now, cancelled-without-landing ≠ ok
├── test_runner_skills.py        # ceiling bumps if needed (deliberate)
├── test_goal_tick.py / test_thin_plan_advance.py   # continuation brief: bounded input, chunk done-ness framing, oversized-chunk re-slice
└── test_task_retry.py / new named tests            # identical-retry refusal for oversized chunks
```

**Structure Decision**: existing layered layout; changes land at layer 5 (runner),
layer 4 (settle), layer 2 (brief assembly), plus one config accessor and one
skill file. No new packages.

## Complexity Tracking

No constitution violations to justify. One deliberate scope note: the runner
gains a minimal checkbox-parser (~30 lines, stdlib) that mirrors
`slice_guard`'s grammar rather than importing it — the runner is a standalone
zero-dep file baked into the image and cannot import host modules; the grammar
is frozen in `contracts/chunk-grammar.md` and both sides are tested against it.
