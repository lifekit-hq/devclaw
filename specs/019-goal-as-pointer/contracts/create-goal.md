# Contract: goal creation surface (referenced lane)

`create_goal` (MCP, devclaw/server/tools/goals.py → GoalService.create_goal)
gains one input and a refusal contract. The issue-less lane (no `issues`) is
byte-compatible with today.

## Input

```jsonc
{
  // existing params unchanged, plus:
  "issues": [411, 447]   // optional; ordered issue numbers on the goal's
                         // project repository. Non-empty ⇒ referenced lane.
}
```

## Referenced-lane semantics

- `done_when` omitted/empty ⇒ the contract is the referenced issues'
  acceptance scenarios, read live at each done-gate round (clarified A).
  `done_when` provided ⇒ explicit contract wins, scenarios not required.
- Free text (objective + note) capped by `DEVCLAW_GOAL_TEXT_BUDGET`.
- Worker briefs for a referenced item are built from a dispatch-time fetch.

## Refusals (hard, nothing persisted, message MUST name rule + input + fixing verb)

| Condition | Message names |
|---|---|
| over budget | the budget, the char count, the referenced issue as destination, the regrade/grooming flow |
| ref not graded ready | the issue, its current grade state, the grading verb (grade_backlog / regrade_intake) |
| ref lacks acceptance section (and done_when defaulted) | the issue, the section convention, OR the explicit done_when alternative |
| ref on another live goal | the holding goal id, the cancel+recreate doctrine |
| cross-repo / duplicate / nonexistent ref | the offending ref and the rule |

## Display

`get_goal` output for a referenced goal shows the refs and the lane; the
defaulted contract is rendered as "acceptance scenarios of #N, #M (live)".
