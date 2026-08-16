# Implementation Plan: Speckit as the Universal Execution Substrate (retire PLAN.md)

**Branch**: `008-speckit-execution-substrate` | **Date**: 2026-08-15 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/008-speckit-execution-substrate/spec.md`

## Summary

Replace `PLAN.md` as devclaw's planning spine with **speckit**, run **inside the
worker** (layer 5, in-sandbox) as plain markdown/commands + the `.specify/` bash
scripts. The MVP is **P1 = US1 + US2**:

- **US1** — the worker brief runs the speckit flow instead of "read/maintain
  PLAN.md"; a dispatched feature goal produces a `specs/NNN-*/` artifact set and
  the **slice-guard reads `tasks.md` checkbox flips** (not `PLAN.md`), fail-closed.
- **US2** — speckit is **universal**: a repo with `.specify/` is adopted (no
  `PLAN.md` written); a bare repo has speckit **installed via a reviewable PR**
  (never a silent commit).

The done-gate is rewired to evaluate against the feature **spec** rather than a
host-side `firmed done_when`. P2 (US3 label-routing, US4 migration) and the
host-cognition **shrink** (#539) are outlined here only — the shrink is a separate
slice gated on Tier-B live proof (relocation, not deletion).

## Technical Context

**Language/Version**: Python 3.12 (devclaw host + `openhands-runner`); bash for the
`.specify/` scripts; speckit v0.16.3 (`workflow-registry.json` + `workflow.yml`).

**Primary Dependencies**: existing — FastMCP (layer 1), OpenHands SDK (layer 5
worker), `claude` CLI over OAuth (cognition), docker (sandbox), `git`/`gh`
(delivery). New: **none** — speckit is already vendored in the repo (`.specify/`);
community workflow packs are a P2 concern, not P1.

**Storage**: `devclaw.db` (SQLite) for goal/task state (unchanged). Planning
artifacts live in the **repo** as `specs/NNN-*/{spec,plan,tasks}.md` — this
replaces the `PLAN.md` blob. `.specify/feature.json` is gitignored per-checkout
state, so it is **not** git-readable at a ref (drives a slice-guard decision below).

**Testing**: pytest, fully stubbed (no docker/no claude), ~1870 tests. Every
behavior change ships a named regression test. `FakeClaude.calls == 0` guard tests
are load-bearing.

**Target Platform**: Linux (host daemon + per-task docker sandbox).

**Project Type**: existing 5-layer agentic system (not greenfield). Changes are
edits to layers 2/4/5 + one MCP-surface tool, not a new tree.

**Performance/Constraints**: **Zero-token idle** (Principle III) — no LLM, ideally
no subprocess, on an idle/blocked tick. The slice-guard runs at settle-time (not
idle); adopt/install detection runs at onboard/dispatch (not idle). Neither adds
idle-path work. **Fail-closed** (Principle V) — a slice-guard crash or missing
`tasks.md` must never silently ship.

**Scale/Scope**: MVP touches ~6 modules (worker brief, slice-guard, onboard tool,
done-gate, + tests). No new lifecycle states in P1 (the `investigating→firming`
collapse is the shrink slice, not the MVP).

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 (below).*

| Principle | Verdict | Note |
|---|---|---|
| I. OAuth only | ✅ pass | No new cognition spawn sites; the worker's speckit run uses the same OAuth `claude`. No API-key path introduced. |
| II. Model-agnostic worker | ✅ pass (**load-bearing**) | speckit is consumed as **plain markdown command-content + bash scripts** vendored in `.specify/`, not Claude-Code slash-command wiring. The worker follows the flow via its skill-discovery (`ls .agent/skills/` + the `.specify/` scripts). Swapping the agent changes only the `ACPAgent` call. See research.md D1. |
| III. Zero-token idle | ✅ pass | No tick-path LLM added. slice-guard = settle-time git subprocess (as today); adopt/install = onboard/dispatch-time. Idle/blocked ticks unchanged; the `FakeClaude.calls == 0` tests stay green. |
| IV. Single writer | ✅ pass | No new state writer. Planning artifacts are repo files (git), not `devclaw.db` rows. Goal transitions unchanged. |
| V. Fail-closed; done is a proposal | ✅ pass (**load-bearing**) | The slice-guard stays fail-closed, now reading `tasks.md`. The done-gate stays grounded — it evaluates against the **spec** (FR-006) instead of `firmed done_when`; a non-achievable spec bounces to needs-human. No gate is weakened. |
| VI. Loud failure | ✅ pass | Bare-repo install is a **reviewable PR**, never a silent commit (US2). A missing/garbled `tasks.md` fails the guard closed with an actionable reason. |
| VII. Fix the class | ✅ pass | This is the class fix for "imposed PLAN.md is blind to repo-native discipline" — speckit everywhere, via speckit's own extension mechanism (FR-009), not a devclaw port. |

**No constitution amendment required.** The spec's Constitution Alignment section
concurs. FR-005 changes *which file* the fail-closed guard reads, not its
consequence.

## Project Structure

### Documentation (this feature)

```text
specs/008-speckit-execution-substrate/
├── spec.md              # done (clarified 2026-08-15)
├── plan.md              # this file
├── research.md          # Phase 0 — key decisions (D1–D8)
├── data-model.md        # Phase 1 — entities + state
├── contracts/           # Phase 1 — internal interface contracts
│   ├── worker-brief.md
│   ├── slice-guard-tasks.md
│   └── onboard-adopt-install.md
├── quickstart.md        # Phase 1 — validation scenarios (Tier A + Tier B)
└── tasks.md             # Phase 2 — /speckit-tasks (NOT created here)
```

### Source code (existing files touched — no new tree)

```text
devclaw/
├── goal/
│   ├── tick.py                 # _advance_brief → run-speckit brief (US1)
│   ├── slice_guard.py          # PLAN.md reader → tasks.md checkbox reader (US1, FR-005)
│   ├── tick_settle.py          # slice-guard call site → tasks.md flips (US1)
│   └── evaluator.py            # done-gate grounds on spec.md success criteria (FR-006)
├── server/
│   └── tools.py                # onboard: adopt-or-install speckit (US2, FR-002/003)
└── delivery/
    └── repo.py / delivery.py   # install-PR path reuse (US2) — reviewable, never silent

openhands-runner/
└── (worker skill content)      # speckit flow as plain-markdown skill + .specify scripts (US1, Principle II)

tests/
├── test_slice_guard_tasks.py   # NEW named regression (SC-003)
├── test_onboard_speckit.py     # NEW named regression (adopt/install, SC-001/SC-004)
└── test_advance_brief_speckit.py  # NEW (worker brief runs speckit, no PLAN.md)
```

**Structure Decision**: brownfield edits to layers 2 (`goal/`), 4/5 (worker
content), and 1 (`server/tools.py`). No new package. The planning-artifact
"storage" moves from a single repo `PLAN.md` to per-feature `specs/NNN-*/` (git),
which is the substance of the change.

## Phasing (this feature)

- **P1 (MVP, this plan — concrete):** US1 (worker runs speckit + slice-guard reads
  tasks.md + done-gate on spec) and US2 (adopt-or-install). Ships with 3 named
  regression tests + a Tier-B live-shakedown (quickstart.md).
- **P2 (outline only):** US3 label-routed ceremony (feature→full / bug→direct,
  with adopted community hotfix/bugfix workflows via FR-009), US4 PLAN.md
  migration.
- **Shrink (#539, outline only, separate slice):** remove the host
  `investigating→firming→decompose` cognition chain once Tier-B proves the worker
  owns planning. **Gated on Tier-B. Relocation, not deletion — do not touch in P1.**

## Complexity Tracking

*No constitution violations — table empty.*

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
