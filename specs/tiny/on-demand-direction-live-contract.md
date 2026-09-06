# TinySpec: on-demand direction reads the live issue contract

**Branch**: fix/on-demand-direction-live-contract
**Date**: 2026-09-06
**Status**: done
**Complexity**: small

## What

`evaluate_goal` (the on-demand direction surface, `GoalService.evaluate_goal`)
judges a referenced goal against the same completion contract the done-gate
judges against: the acceptance sections of its issues, read live. Today it
passes the goal's stored `done_when` straight to the evaluator, and for a
pointer goal that field is empty by design (spec 019) - so the evaluator sees
`done_when: (not specified)`, returns `needs_human`, and that verdict lands in
`goal_status.last_eval_verdict` where `list_goals` and the console show it as
the goal's direction.

## Context

Observed 2026-09-06 on `issue-819-nested-npmrc-2026-09-05`: three on-demand
direction calls each said "done_when is literally (not specified) ... nothing
to decompose" while the done-gate on the same goal logged "done-gate contract
from live issue(s) #819 - revision d23f6ee570ac". Two readers of one contract
disagreed on whether it exists.

The done-gate resolves the contract in `tick_donegate._live_contract` (spec
019 US2, load-bearing: absence blocks the round). The on-demand path was
written before pointer goals existed and never grew the same step. The
`corrections` an on-demand verdict emits are appended as steering, so an
ungrounded verdict is not telemetry-only - it can steer the next dispatch.

| File | Role |
|------|------|
| `devclaw/goal/service.py` | Modified - `evaluate_goal` resolves the scenario contract before evaluating |
| `devclaw/server/tools/goals.py` | Modified - a contract read failure surfaces as a `ToolError`, not a stack trace |
| `tests/test_done_when_scenarios.py` | Modified - the load-bearing-contract class test grows the on-demand case; no sibling |

## Requirements

1. For a goal with `issue_refs` and an empty `done_when`, the on-demand
   evaluation reads the same contract `scenarios_contract` builds for the
   done-gate, and the evaluator prompt carries it.
2. A goal with an explicit `done_when` is unaffected.
3. The read is load-bearing (spec 019 convention for completion contracts):
   a fetch failure or a missing acceptance section raises a legible error to
   the caller. The evaluator never runs against an empty contract, and no
   verdict is recorded.
4. On demand means on demand: the tool does NOT block the goal on a failed
   read (the done-gate owns blocking); it reports and stops.

## Plan

1. In `GoalService.evaluate_goal`, after loading the goal: if `issue_refs`
   and no `done_when`, call `_issue_ref.scenarios_contract(repo_url,
   issue_refs, _issue_ref.fetch_issue)` and substitute the result via
   `replace(g, done_when=contract)`. Wrap `IssueRefError` /
   `MissingAcceptance` in a `ValueError` carrying the reason.
2. In the MCP tool, map that `ValueError` to `ToolError`.
3. Extend `tests/test_done_when_scenarios.py` with one on-demand case: the
   evaluator's prompt carries the live scenario text, and a fetch failure
   raises before any cognition call (`FakeClaude.calls == 0`).

## Rejected alternatives

- **Reuse `tick_donegate._live_contract` directly.** It transitions the goal
  to `blocked` on failure. The on-demand surface is documented as
  "reports + steers, does not block on demand"; blocking from a read-only
  probe would let an operator's status check change goal state.
- **Degrade to `""` and let the evaluator say needs_human.** That is the bug.
  A completion contract is a load-bearing input, not a best-effort collector
  (`.claude/rules/cognition-prompts.md`, spec 019).

## Tasks

- [x] `evaluate_goal` resolves the live contract for pointer goals
- [x] `ToolError` on an unreadable contract
- [x] Extend the scenarios class test with the on-demand case

## Done When

- [x] On-demand direction for a pointer goal grades against the issue's
      acceptance text, never `(not specified)`
- [x] An unreadable contract raises before cognition runs
- [x] Full suite + `ruff check .` + `mypy` + `lint-imports` green
