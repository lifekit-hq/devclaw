# Implementation Plan: A provider-side transient pauses and resumes

**Branch**: `036-server-transient-pause` | **Date**: 2026-09-06 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/036-server-transient-pause/spec.md`

## Summary

A provider outage (`529 Overloaded`, `server_error`) is currently classified
`TRANSIENT` — retry now — so it takes the ordinary task-failure path and
burns the goal's dispatch cap. This arc gives it its own `FailureKind`,
`SERVER_ERROR`, and puts that kind in `PAUSING_KINDS`. Everything downstream
is then existing machinery: the queue's pausing branch already snapshots WIP,
requeues instead of failing, and returns `_PAUSED` on the FIRST failed
attempt; the heartbeat already skips all cognition while paused, pings once
per episode, clears the pause on expiry and pings the resume. The only new
code is the classification, its backoff policy, and the two ping strings that
would otherwise lie ("usage limit"). No new table, no new verb, no tick-path
work, no cognition call.

## Technical Context

**Language/Version**: Python 3.11 (existing repo toolchain)

**Primary Dependencies**: none new — stdlib `re`/`enum` in `loom/limits.py`

**Storage**: none new in US1 (the pause rides the existing `meta` keys `paused_until` / `pause_reason` / `pause_notified` owned by `StateStore.control`); US2 adds two `meta` keys for the episode ladder — same single writer

**Testing**: pytest, fully stubbed — extends the existing pause-class tripwires (`tests/test_limits.py`, `tests/test_rate_limit_pause.py`, `tests/test_goal_rate_limit.py`); no new test module

**Target Platform**: the deployed VPS instance (layers 2 and 4); US3 touches layer 5 (runner)

**Project Type**: internal harness machinery — MCP tool surface untouched

**Performance Goals**: unchanged; classification is one extra regex on an already-failed call

**Constraints**: zero-token idle (Principle III) — the pause path only runs on an already-failed call, and while paused the heartbeat's cognition is skipped as today

**Scale/Scope**: fleet-wide by design — the outage is account-wide, so one pause gates every project

## Constitution Check

- [x] **I. OAuth only** — no new spawn site; the strip logic is untouched.
- [x] **II. Model-agnostic worker layer** — US1/US2 are host-side (layers 2/4). US3 touches `runner/runner.py`'s vendored classifier only, which is plain Python with no model-specific wiring.
- [x] **III. Zero-token idle** — no new tick-path work. The pause is set from an already-failed call; while it holds, `tick_all` returns `RATE_LIMITED` for every goal before any cognition, so `FakeClaude.calls == 0` stays green (SC-003).
- [x] **IV. Single writer** — the pause is written through `StateStore.control` exactly as quota/auth write it; no new writer, no view read back.
- [x] **V. Verification fails closed** — no gate is relaxed. A paused task is requeued, never settled `done`; the `MAX_PAUSE_REQUEUES` bound still fails a task that rides the loop forever, and every other fail-closed branch (worker block, review crash, OOM, prompt-too-long) keeps its position relative to the pause check.
- [x] **VI. Loud failure** — the pause is announced once, accurately, with the reason and the resume time; the requeue is logged to stderr like the quota requeue; a bounded episode still ends in a real failure with the real reason.
- [x] **VII. Fix the class** — the class is "a failure with no agent cause and no retry that can succeed inside it"; the three parked goals are the instance. The fix lands in the shared classifier, so cognition calls, dispatched tasks and the heartbeat all inherit it at once.
- [x] **VIII. Cognitive guardrail?** — **No guardrail is added.** `classify_failure` is a brake (the pause-and-resume family), which the principle names as a structural invariant that is never shed. The change widens an existing brake's trigger set; it does no thinking the model should do.

## Project Structure

### Documentation (this feature)

```text
specs/036-server-transient-pause/
├── spec.md              # clarified spec (7 clarify Qs, autonomous session)
├── plan.md              # this file
├── research.md          # Phase 0 — the four design decisions
├── quickstart.md        # Phase 1 — validation scenarios
└── tasks.md             # per-story tasks
```

(`contracts/` and `data-model.md` omitted: no external interface and no
persisted-shape change in US1. US2's two `meta` keys and US3's result-payload
tag are specified in research.md D3/D4 — both are single-field additions to
shapes that already exist.)

### Source Code (repository root)

```text
devclaw/loom/
├── limits.py            # US1: FailureKind.SERVER_ERROR, the STRONG provider
│                        #   pattern, PAUSING_KINDS + RETRY_NOW_KINDS,
│                        #   SERVER_ERROR_PAUSE_S in pause_seconds
│                        # US2: the episode ladder (escalate(step) helper)
└── __init__.py          # re-export the new names

devclaw/llm_call.py      # US1: cognition retries RETRY_NOW_KINDS (transient +
                         #   server) instead of TRANSIENT alone (FR-005)

devclaw/goal/tick.py     # US1: provider-outage wording for the pause + resume
                         #   pings; the persisted episode kind

devclaw/queue/settle.py  # US1: no change (the pausing branch is kind-agnostic)
                         # US2: pass the episode step into pause_seconds
                         # US3: honour result["status"] == "server_error"

devclaw/state_store/control.py   # US2 only: the episode counter accessors

runner/runner.py         # US3 only: tag a provider outage structurally

tests/
├── test_limits.py       # extend the classify parametrization + the pausing-flag
│                        #   and priority-order cases (never a sibling module)
├── test_rate_limit_pause.py     # extend the queue-side pause cases with the
│                                #   provider-outage wording (requeue, not fail)
└── test_goal_rate_limit.py      # extend the ping cases: accurate wording,
                                 #   once per episode, resume
```

## Phase 0 → research.md

Four decisions: new kind vs. widening TRANSIENT; the strong/weak pattern
split; where the episode ladder lives; how the runner tag interacts with the
regex fallback. See [research.md](./research.md).

## Phase 1 → quickstart.md

Stubbed validation scenarios per story. See [quickstart.md](./quickstart.md).

## Slicing (unit of review; the whole spec is the commitment)

- **PR 1 (US1 — FR-001…FR-008)**: the classification + the pause policy
  constant + the cognition retry set + the two ping strings, with the
  pause-class tripwires extended. Files: `devclaw/loom/limits.py`,
  `devclaw/loom/__init__.py`, `devclaw/llm_call.py`, `devclaw/goal/tick.py`,
  the three test modules above, plus `CLAUDE.md` + `docs/architecture.md`
  wherever they enumerate the pausing kinds. Constraint discovered while
  planning: `devclaw/queue/settle.py` needs NO edit — its pausing branch
  keys on `Classification.is_pausing` and already returns before the retry
  loop's `continue`, which is exactly what kills the "(failed after 2
  attempts)" burn.
- **PR 2 (US2 — FR-009, FR-010)** — LANDED 2026-09-06. Files:
  `devclaw/loom/limits.py` (pure `escalated_pause_seconds(step)`, reached via a
  new `episode_step` kwarg on `pause_seconds` so the backoff policy keeps one
  home), `devclaw/state_store/control.py` (episode counter, single writer),
  `devclaw/state_store/core.py` (`mark_done` ends the episode),
  `devclaw/queue/settle.py` + `devclaw/goal/tick.py` +
  `devclaw/goal/engine.py` (take the step on set). Constraint held: the counter
  resets on success, or a long-lived instance ratchets to the 30-minute ceiling
  permanently — `mark_done` is the ONE choke point for "a successful session",
  so the reset does not have to be repeated at five settle sites. Constraint
  discovered while implementing: the counter is read-and-advanced in a single
  locked call, because the queue pump and the heartbeat both set pauses and a
  separate read+write would hand both the same step.
- **PR 3 (US3 — FR-011)**: the structural tag. Files: `runner/runner.py`
  (vendored pattern + `_failure_result` branch), `devclaw/queue/settle.py`
  (honour the tag), `devclaw/engine/__init__.py` (document the status in the
  result contract), `tests/test_runner_wrappers.py` cases. Constraint: the
  vendored runner patterns must stay in sync with `loom/limits.py` — the file
  already carries that warning, and US3 adds a fourth pattern to it.
