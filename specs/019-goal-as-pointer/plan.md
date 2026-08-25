# Implementation Plan: Goal-as-Pointer

**Branch**: `019-goal-as-pointer` | **Date**: 2026-08-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/019-goal-as-pointer/spec.md`

## Summary

Goals gain first-class, ordered issue references validated hard at the
doorway (ready-graded, same-project, unclaimed, within a free-text budget),
fetched fresh at every dispatch and done-gate boundary through the existing
injectable `gh` seam, with `done_when` defaulting to the referenced issues'
acceptance scenarios read live at evaluation time (clarified A/A/A). The
issue-less lane keeps today's behavior, recorded explicitly.

## Technical Context

**Language/Version**: Python 3.11 (existing repo toolchain)

**Primary Dependencies**: stdlib; `gh` CLI subprocess for issue reads via an
injectable runner (precedent: `devclaw/goal/self_issue.py`'s gh protocol,
`devclaw/goal/remote_checks.py`'s `_gh`)

**Storage**: `goal.yaml` gains additive fields (`issue_refs`, lane implied
by their presence) loaded with dataclass defaults — no SQLite schema change;
exclusivity is checked against live goals' stored refs at creation

**Testing**: pytest, fully stubbed — a `FakeIssueFetcher` injected at the
layer-2 call sites; zero network in tests

**Target Platform**: Linux server (VPS instance) + dev host

**Project Type**: existing service — layers touched: 1 (`server/tools/goals.py`
param surface + refusal messages), 2 (`goal/service.py` create validation,
`goal/tick_dispatch.py` brief injection, done-gate call site scenario fetch),
new layer-2 module `goal/issue_ref.py` (parse/validate/fetch/extract),
`config.py` (budget)

**Performance Goals**: creation validation ≤ one `gh` read per ref;
dispatch adds one `gh` read per referenced item; idle ticks add zero work

**Constraints**: zero cognition added; zero fetches on idle ticks; the
contract fetch (US2) is load-bearing and fails LOUD-and-blocking, unlike the
best-effort repo-context collectors; refusals never persist partial state

**Scale/Scope**: single-digit refs per goal; validation is a few gh calls at
human-initiated creation time

## Constitution Check

*GATE: evaluated against constitution v2.4.0.*

- **I. OAuth only** — PASS. No cognition added; `gh` rides the host's
  existing GitHub auth, no Anthropic-key surface.
- **II. Model-agnostic worker layer** — PASS. The worker/runner is
  untouched; the issue content reaches it as brief text through the existing
  dispatch plumbing, not as tool wiring.
- **III. Zero-token idle** — PASS. All fetches sit at the dispatch and
  done-gate boundaries (both post-`should_plan`, non-idle); guard tests
  extend to assert zero fetcher calls on idle/blocked ticks.
- **IV. Single writer to state** — PASS. Goal facts stay in `goal.yaml`
  written at creation; no new writers; exclusivity is a read-check inside
  the existing create path.
- **V. Verification fails closed; "done" is a proposal** — PASS, and
  sharpened: the scenario-default contract is fetched for the done-gate; an
  unfetchable contract BLOCKS the gate round legibly (never evaluates
  against emptiness). Deviation from the best-effort collector convention in
  `.claude/rules/cognition-prompts.md` is deliberate and documented there in
  the same PR: a completion CONTRACT is load-bearing input, not optional
  grounding — degrade-to-`""` on it would be a silently-weakened gate.
- **VI. Loud failure** — PASS by design: every doorway refusal names rule,
  offending input, and fixing verb (FR-010); unfetchable refs block with
  the existing lost-ref semantics.
- **VII. Fix the class** — PASS: kills the frozen-copy class (not the #684
  instance), and aligns human-filed goals with 007's claim semantics.
- **Workflow** — spec → clarify (3 Qs, done) → this plan → tasks →
  implement; one reviewable PR per story; whole spec is the commitment.
- **Doctor rule (spec 016 FR-014)** — `goal.yaml` gains additive fields:
  the shipping PR adds a project-doctor check that referenced-goal records
  parse (and refs are well-formed) plus a seeded-fault test.

**Post-design re-check**: PASS — the one convention deviation (load-bearing
contract fetch vs best-effort collectors) is named above and carries its
rule-doc update; no Complexity Tracking entries needed.

## Project Structure

### Documentation (this feature)

```text
specs/019-goal-as-pointer/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── create-goal.md   # the changed creation surface + refusal contract
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
devclaw/
├── goal/
│   ├── issue_ref.py         # NEW — parse/validate refs, injectable fetcher
│   │                        #   (gh api repos/{repo}/issues/{n}), readiness
│   │                        #   check, acceptance-scenario extraction
│   ├── models.py            # Goal.issue_refs (additive, defaulted)
│   ├── service.py           # create_goal validation chain (budget, ready,
│   │                        #   same-project, unclaimed, scenarios-present)
│   ├── tick_dispatch.py     # dispatch boundary: fetch → brief section;
│   │                        #   closed/not-ready item skip; unfetchable block
│   ├── tick_donegate.py / tick_settle.py  # done-gate call site: live
│   │                        #   scenario fetch when done_when defaulted
├── config.py                # + DEVCLAW_GOAL_TEXT_BUDGET (one home/default)
├── doctor/checks_project.py # referenced-goal record parses; refs well-formed
└── server/tools/goals.py    # + issues param; refusal messages (layer 1 stays
                             #   protocol-only — validation lives in service)

tests/
├── test_goal_issue_refs.py        # doorway validation matrix (named tests)
├── test_issue_ref_freshness.py    # dispatch-time fetch, closed-skip, block
├── test_done_when_scenarios.py    # live-at-eval contract, override, absent
└── test_goal_tick.py              # idle guard extended: zero fetcher calls
```

**Structure Decision**: one new module (`goal/issue_ref.py`) owns everything
reference-shaped so the doorway, dispatch, and done-gate consume one seam;
all other changes land in the existing owner of each concern.

## Phase 0 → research.md

All decisions recorded in research.md: ref shape (numbers against the
project's repo), fetch seam (gh, injectable, one module), budget home and
default, scenario-extraction convention (spec 015's `## Acceptance` shape),
exclusivity read, lane recording, and the load-bearing-fetch deviation.

## Phase 1 → data-model.md, contracts/, quickstart.md

Generated alongside this plan.
