# Tasks: A provider-side transient pauses and resumes

**Input**: Design documents from `/specs/036-server-transient-pause/`

**Prerequisites**: plan.md, spec.md (clarified), research.md, quickstart.md

**Tests**: This feature touches the pause-and-resume brake — a tripwire class — so the named tests below are REQUIRED. They EXTEND the existing quota/auth pause cases; no sibling module, no instance test (rules/testing.md).

**Organization**: One phase per user story = one reviewable PR each (plan.md slicing).

## Phase 1: User Story 1 — a provider outage pauses instead of failing (P1) 🎯 MVP

**Goal**: `529`/`server_error` classifies as a pausing kind, so the queue requeues instead of failing and the goal's dispatch cap is untouched; the owner ping tells the truth.

**Independent test**: quickstart Scenarios 1–3.

- [x] T001 [US1] `devclaw/loom/limits.py`: add `FailureKind.SERVER_ERROR`; add the STRONG provider pattern (`529`, `overloaded`/`overloaded_error`, `server_error` token, `API Error: 5xx`) checked AFTER quota/rate and BEFORE `_TRANSIENT`; move `529`/`overloaded` out of `_TRANSIENT`; add `SERVER_ERROR` to `PAUSING_KINDS` (FR-001..FR-003)
- [x] T002 [US1] `devclaw/loom/limits.py`: `SERVER_ERROR_PAUSE_S` + the `pause_seconds` branch (stated hint wins, else the base); `RETRY_NOW_KINDS` as the one home for "retry in-process" policy
- [x] T003 [P] [US1] `devclaw/loom/__init__.py`: re-export the new names beside the existing limits exports
- [x] T004 [US1] `devclaw/llm_call.py`: the cognition retry loop keys on `RETRY_NOW_KINDS`, so a provider outage exhausts the in-process retries before it reaches the pause path (FR-005)
- [x] T005 [US1] `devclaw/goal/tick.py`: provider-outage wording for the once-per-episode pause ping (never "usage limit", not `critical`) and for the matching resume ping; persist the episode kind (FR-007)
- [x] T006 [US1] Tests — extend existing class cases only: `tests/test_limits.py` (classify parametrization, pausing-flag, priority order), `tests/test_rate_limit_pause.py` (queue requeues + pauses on the observed 529 wording, one engine call), `tests/test_goal_rate_limit.py` (accurate ping once, matching resume ping)
- [x] T007 [US1] Docs in the same PR: `CLAUDE.md` hardening bullet (the pausing kinds are enumerated there and nowhere else — `docs/architecture.md` names the pause only as a brake, so it stayed correct) + the `DEVCLAW_COGNITION_RETRIES` row in `docs/reference/env-vars.md`, with its `docs/INDEX.md` currency tag

**Checkpoint**: US1 alone is the MVP — a provider outage costs zero dispatches and resumes itself; coverage is ~25 min of outage (5 requeues × 5 min) until US2 lands.

**Checkpoint (US2, landed 2026-09-06)**: the same five requeues now span ~95 min (5/10/20/30/30). US3 was CUT 2026-09-10 (see `specs/README.md`); detection stays host-side. Formerly: depends on the 529 wording surviving the ACP → runner → settle hops.

## Phase 2: User Story 2 — a sustained outage escalates, bounded (P2)

**Goal**: consecutive pauses in one episode double from 5 to 30 minutes and reset on success.

**Independent test**: quickstart Scenario 4.

- [x] T008 [US2] `devclaw/loom/limits.py`: `escalated_pause_seconds(step)` — pure, doubling, clamped at `SERVER_ERROR_MAX_PAUSE_S`; a stated hint still wins (FR-009), reached through a new `episode_step` kwarg on `pause_seconds` so the backoff policy keeps ONE home
- [x] T009 [US2] `devclaw/state_store/control.py`: episode-step accessors beside `pause_notified` — `next_pause_episode_step` (reads-and-advances under the store lock), `pause_episode_step`, `clear_pause_episode`; single writer, no new table
- [x] T010 [US2] `devclaw/queue/settle.py` + `devclaw/goal/tick.py` (+ the `devclaw/goal/engine.py` accessor seam): take the step when a `SERVER_ERROR` pause is set; clear it on a productive settle so the ladder cannot ratchet permanently (FR-010)
- [x] T011 [US2] Tests: extend T006's cases with the ladder sequence, the clamp, the stated-hint precedence, and the reset-on-success
- [x] T012 [US2] Docs + `/ship` ritual; PR 2


## Notes

- US1 PR (2026-09-06), branch point `8499fb6`: **1413 passed, 5 skipped** — +9 cases over the branch point, all of them extensions of existing pause-class tests (4 classify parametrizations, 1 named regression, 4 parametrize rows across the queue/heartbeat pause cases); `ruff check .` clean; `mypy` clean (149 files)
- `devclaw/queue/settle.py` needed NO edit for US1: its pausing branch keys on `Classification.is_pausing` and returns `_PAUSED` before the retry loop's `continue`, so the first provider-shaped failure ends the attempt loop — that is what removes the observed "(failed after 2 attempts)" burn
- The cap is protected by construction, not by new accounting: `actions_dispatched` is charged at dispatch (`tick_dispatch`) and refunded on a productive settle (`tick_settle`); a requeued task never settles, so the SAME dispatch resumes after the outage and keeps its refund, where the old failure path made the charge stick and let the goal spend another one
- Never mint a sibling test: every case above extends the named pause-class test it belongs to
- US2 PR (2026-09-06): **1437 passed, 5 skipped** (+24 over US1's 1413 — all parametrize rows on the three existing pause-class tests, no new module); `ruff check .` clean; `mypy` clean (144 files). `lint-imports` could not run in the sandbox (import-linter not installed) — the change adds no cross-package import: `loom` stays a pure leaf, the two callers reach the store through seams they already held
- "A productive settle" is `StateStore.mark_done` — ONE choke point for all five settle sites, guarded by the same `rowcount == 1` exactly-once check the eval-outcomes projection uses, so a no-op re-settle cannot end an episode. Precedent for the cross-concern write: `set_global_pause` already records a problem from the control plane
- `next_pause_episode_step` reads AND advances under the store lock: the queue pump and the heartbeat both write pauses, and two readers of a plain counter would hand out the same step and pause twice for the same length
- A step is one PROBE ROUND, not one failure (found in self-review): up to `DEVCLAW_MAX_CONCURRENT` tasks are in flight when an outage starts and all fail within seconds, so counting each would put the FIRST round at the ceiling — and because the pause write is last-one-wins, the length actually applied could be any of the steps burned. A failure arriving while this episode's pause is still in force reuses the step already in force
- **Known residual bound**: the episode ends only on a productive settle (FR-010's own words). A session that runs to a REAL failure, and a successful goal-cognition call, both prove the provider answered but leave the counter where it is — so an outage a week later can start at the 30-minute ceiling instead of 5. Bounded (the ceiling is the ceiling) and self-healing (the next task that settles `done` clears it); a second reset mechanism for it was rejected as more machinery than the 25-minute worst case is worth. Revisit if the live instance shows a long-lived high step
