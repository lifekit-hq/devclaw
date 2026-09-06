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

## Phase 2: User Story 2 — a sustained outage escalates, bounded (P2)

**Goal**: consecutive pauses in one episode double from 5 to 30 minutes and reset on success.

**Independent test**: quickstart Scenario 4.

- [ ] T008 [US2] `devclaw/loom/limits.py`: `escalated_pause_seconds(step)` — pure, doubling, clamped at `SERVER_ERROR_MAX_PAUSE_S`; a stated hint still wins (FR-009)
- [ ] T009 [US2] `devclaw/state_store/control.py`: episode-step accessors beside `pause_notified` (bump, read, clear) — single writer, no new table
- [ ] T010 [US2] `devclaw/queue/settle.py` + `devclaw/goal/tick.py`: bump the step when a `SERVER_ERROR` pause is set; clear it on a productive settle so the ladder cannot ratchet permanently (FR-010)
- [ ] T011 [US2] Tests: extend T006's cases with the ladder sequence, the clamp, the stated-hint precedence, and the reset-on-success
- [ ] T012 [US2] Docs + `/ship` ritual; PR 2

## Phase 3: User Story 3 — the sandbox reports the outage structurally (P3)

**Goal**: detection no longer depends on the wording surviving the ACP → runner → settle hops.

**Independent test**: quickstart Scenario 5.

- [ ] T013 [US3] `runner/runner.py`: vendored provider pattern (kept in sync with `loom/limits.py`) + `_failure_result` emitting `status="server_error"` with `retry_after` and the original text (FR-011)
- [ ] T014 [US3] `devclaw/queue/settle.py`: honour `result["status"] == "server_error"` independently of the text, mirroring the `rate_limited` branch; `devclaw/engine/__init__.py` documents the status in the result contract
- [ ] T015 [US3] Tests: extend the runner wrapper cases (fake ACP agent fails with 529 wording → tagged result) and the settle case (tag alone pauses)
- [ ] T016 [US3] Docs + `/ship` ritual; PR 3

---

## Notes

- US1 PR (2026-09-06), branch point `8499fb6`: **1413 passed, 5 skipped** — +9 cases over the branch point, all of them extensions of existing pause-class tests (4 classify parametrizations, 1 named regression, 4 parametrize rows across the queue/heartbeat pause cases); `ruff check .` clean; `mypy` clean (149 files)
- `devclaw/queue/settle.py` needed NO edit for US1: its pausing branch keys on `Classification.is_pausing` and returns `_PAUSED` before the retry loop's `continue`, so the first provider-shaped failure ends the attempt loop — that is what removes the observed "(failed after 2 attempts)" burn
- The cap is protected by construction, not by new accounting: `actions_dispatched` is charged at dispatch (`tick_dispatch`) and refunded on a productive settle (`tick_settle`); a requeued task never settles, so the SAME dispatch resumes after the outage and keeps its refund, where the old failure path made the charge stick and let the goal spend another one
- Never mint a sibling test: every case above extends the named pause-class test it belongs to
