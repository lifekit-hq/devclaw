# TinySpec: the worker closes its own review findings; devclaw's goals run under trust (spec 037 cuts 1 + 3)

**Spec**: `specs/037-loop-shape-demolition/spec.md` (US1 + US3 — ruled 2026-09-06, executed via the tinyspec lane)
**Branch**: demolish/worker-self-review
**Date**: 2026-09-06
**Status**: done (goes live with the next sandbox-image rebuild + deploy)
**Complexity**: small

## What

1. **One skill section** (`runner/skills/_writes-code/45-self-review.md`): when
   the change verifies green, the worker reviews its own diff against the
   contract in a fresh context (a subagent that did not write it), fixes every
   finding in the same session, repeats until clean, and names the outcome in
   its summary. The quality bar's old "before finishing, re-read your diff"
   paragraph folds into it — one home for the rule. Instruction only, per the
   ruling (constitution IX: instruction before brake); the scorecard's rounds
   median is the check. The host's independent done-check stays as the one
   read per proposal.
2. **`devclaw.json`**: `strictnessDefault` `strict` → `trust` for devclaw's own
   goals. The per-increment adversarial review gate — the largest failure
   source in the problems catalog (117 OOM rows) — stops running on the repo
   where the owner reviews every PR. CI + the done-gate + that review are the
   surface, as for every other project. `set_goal_strictness` still opts a
   goal back.

## Why it is relocation

The review of an increment is repo-varying judgment; it belongs to the agent
with the repo in front of it, not to a second sandbox that reads a summary.
Today's serial one-finding-per-round loop exists only because the fixer and
the reviewer are different sessions (devclaw-030: 18 dispatches, 8 gate
rounds, one gap per round). The host keeps the verdict of record.

## Context

| File | Change |
|------|--------|
| `runner/skills/_writes-code/45-self-review.md` | new — the rule, stated once |
| `runner/skills/_writes-code/10-quality-bar.md` | the "before finishing" paragraph removed (moved) |
| `devclaw.json` | `strictnessDefault: trust` |

No test: an instruction change is measured by the live scorecard, not the stubbed suite (tests-to-tripwires ruling).

## Going live

Skills are baked into the sandbox image (`/opt/devclaw/skills/`), so this needs
the image rebuild the deploy pipeline does whenever `runner/` changes, then a
deploy. Read the scorecard 14 days after: rounds median 2 → 1 is the number.

## Done When

- [x] The skill section exists and the quality bar no longer duplicates it
- [x] devclaw's manifest default is `trust`
- [x] Suite green (docs map, skills bundle guards), ruff + mypy clean
- [ ] Live: rounds median and interventions per goal read from the scorecard after 14 days
