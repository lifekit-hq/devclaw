# Implementation Plan: Scorecard Measures the Ratchet

**Branch**: `018-scorecard-ratchet` | **Date**: 2026-08-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/018-scorecard-ratchet/spec.md`

## Summary

Make the three headline scorecard metrics true — per-goal convergence
(first-pass rate + rounds-to-close, requiring the round count to survive the
close), per-PR ground-truth merge rate (distinct PRs, state from GitHub via a
persisted ledger refreshed off-tick at cycle-report time — clarified option
B), and a human/machine steering split — then surface the agreed autonomy
thresholds as configuration-driven pass/fail so the spec 007 flip is decided
on numbers that match a hand audit.

## Technical Context

**Language/Version**: Python 3.11 (existing repo toolchain)

**Primary Dependencies**: stdlib + sqlite3 (state), `gh` CLI subprocess for
platform reads (existing seam: `devclaw/goal/remote_checks.py::_gh`)

**Storage**: SQLite `devclaw.db` — new `goal_convergence` and `pr_ledger`
tables in `devclaw/state_store/schema.py` (same file already hosts
`eval_outcomes` and `cycle_reports`, the two precedent ledgers)

**Testing**: pytest, fully stubbed (no docker/claude/gh); seeded-store
fixtures per `tests/goal_fakes.py` conventions; a `FakeRemoteStates` injected
checker mirroring `remote_checks.default_checker()`'s injection pattern

**Target Platform**: Linux server (VPS instance) + dev host

**Project Type**: existing service — layers touched: 2 (GoalService close
transitions + cycle-report step), state_store (schema + writers),
telemetry (read-side), server tools/routes (surface), project_registry
(bench flag), config.py (thresholds)

**Performance Goals**: scorecard read stays instant (pure SQL, no network);
ledger refresh ≤ 1 `gh` lookup per distinct undecided in-window PR, capped,
once per cycle

**Constraints**: zero cognition anywhere on these paths; zero
subprocess/network on idle ticks (refresh runs only inside the once-per-cycle
report emission); single-writer per table; loud staleness

**Scale/Scope**: tens of goals / PRs per window; SQL scans are trivial

## Constitution Check

*GATE: evaluated against constitution v2.4.0.*

- **I. OAuth only** — PASS. No cognition added anywhere; `gh` uses the host's
  existing GitHub auth (same class as delivery/remote-checks), no Anthropic
  key surface.
- **II. Model-agnostic worker layer** — PASS (not touched). No worker skills,
  no runner changes.
- **III. Zero-token idle** — PASS by construction. Telemetry read stays pure
  SQL. The only new subprocess work (ledger refresh) is hosted inside
  `GoalService._maybe_emit_cycle_report`, which already fires exactly once
  per cycle at window close — never on an idle tick. New zero-token/
  zero-subprocess guard test asserts idle ticks make no `gh` calls.
- **IV. Single writer to state** — PASS. `goal_convergence` written only by
  the goal close/cancel transitions through GoalService's store handle (the
  `record_cycle_report` precedent); `pr_ledger` written only by the
  cycle-report refresh step. Telemetry reads both, writes nothing. Markdown
  views untouched.
- **V. Verification fails closed** — N/A-with-a-note: the scorecard is
  telemetry, not a gate; FR-004 deliberately fails LOUD (unknown buckets +
  staleness stamps), not closed, and the spec says so. No gate semantics
  change.
- **VI. Loud failure** — PASS, this feature is largely an instance of it
  (unknown PR states, `rounds unknown` bucket, `as_of` staleness stamp,
  refresh cap disclosure).
- **VII. Fix the class** — PASS: replaces row-counting with identity-counting
  (PRs), verdict-weighting with goal-weighting, and name-vs-meaning drift
  with the FR-011 rule.
- **Workflow** — spec → clarify (done, 1 Q) → this plan → tasks → implement;
  each story lands as one reviewable PR; whole spec is the commitment.
- **Doctor rule (spec 016 FR-014)** — the PR that adds the two tables and
  the project `bench` field ships matching doctor checks + seeded-fault
  tests.

**Post-design re-check**: no violations introduced by Phase 1 design; no
Complexity Tracking entries needed.

## Project Structure

### Documentation (this feature)

```text
specs/018-scorecard-ratchet/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── scorecard-output.md   # the read-surface wire contract
└── tasks.md             # Phase 2 (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
devclaw/
├── state_store/
│   ├── schema.py            # + goal_convergence, pr_ledger tables
│   ├── evals.py             # + writers/readers: record_goal_close,
│   │                        #   upsert_pr_states, undecided_pr_urls (or a
│   │                        #   new convergence.py module if evals.py grows)
├── goal/
│   ├── tick_donegate.py     # close path: record convergence BEFORE reset
│   ├── service.py           # cancel paths record abandonment; cycle-report
│   │                        #   step gains the bounded pr_ledger refresh
│   ├── remote_checks.py     # + pr_state lookup helper on the existing _gh
│   │                        #   seam (injected for tests)
├── telemetry.py             # compute_scorecard: corrected metrics,
│                            #   thresholds pass/fail, field renames
├── config.py                # + DEVCLAW_RATCHET_* thresholds (single doorway)
├── project_registry.py      # + Project.bench flag (+ update_project surface)
├── doctor/
│   ├── checks_instance.py   # + tables-exist / ledger-freshness checks
├── server/tools/observability.py  # docstring/output shape follows telemetry
└── cli.py                   # format_scorecard rendering update

tests/
├── test_telemetry_scorecard.py     # seeded-store metric definitions (named)
├── test_goal_convergence_record.py # close/cancel writers + unknown bucket
├── test_pr_ledger_refresh.py       # refresh bounds, unknowns, staleness
└── test_goal_tick.py               # extended zero-subprocess idle guard
```

**Structure Decision**: no new packages; every change lands in the existing
module that owns that concern (schema in schema.py, writers beside
`eval_outcomes`'s in state_store, refresh inside the cycle-report step it
rides, read-side entirely in telemetry.py).

## Phase 0 → research.md

All NEEDS CLARIFICATION were resolved (spec Clarifications + the decisions
recorded in research.md): ledger-vs-poll (B, from clarify), convergence
persistence shape (ledger table following the `eval_outcomes` precedent, not
an events-scan), platform seam (`remote_checks._gh` pattern with injected
checker), thresholds home (config.py env doorway), bench flag home
(project_registry field), legacy field disposition (remove, per FR-011).

## Phase 1 → data-model.md, contracts/, quickstart.md

Generated alongside this plan. The wire contract for the corrected scorecard
output (the surface CLI/MCP/console consume) lives in
`contracts/scorecard-output.md`; entity shapes and state transitions in
`data-model.md`; end-to-end validation runs in `quickstart.md`.
