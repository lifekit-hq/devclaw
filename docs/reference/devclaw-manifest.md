# `devclaw.json` — the per-project manifest

**Status**: CURRENT — written with spec 016 US2 (2026-08-24).

A small, **human-owned** JSON file at the root of every operated repo: what
devclaw used to infer, declared and PR-reviewed. Devclaw reads it through one
doorway (`devclaw/project_manifest.py`) and writes it exactly once — a
mechanical seed on the speckit install PR. Runtime never writes it; the
onboarding agent is instructed not to touch it.

```json
{
  "$schema": "https://raw.githubusercontent.com/lifekit-hq/devclaw/main/docs/reference/devclaw-manifest.schema.json",
  "schemaVersion": 1,
  "boilerplateRevision": 1,
  "strictnessDefault": "trust",
  "surface": "app",
  "verifyCmd": "dotnet test",
  "stack": ["dotnet", "angular"]
}
```

Machine schema: [`devclaw-manifest.schema.json`](./devclaw-manifest.schema.json).

## Fields

| key | values | consumer |
|---|---|---|
| `schemaVersion` (required) | int ≥ 1 | the doorway — a version newer than the instance fails loud ("instance too old"), never a partial parse |
| `boilerplateRevision` | int | doctor (US3): compared to the instance's `BOILERPLATE_REVISION`; re-onboard migrates |
| `strictnessDefault` | `trust` \| `strict` | gate-strictness default for goals that never explicitly chose |
| `surface` | `app` \| `library` | browser-E2E gate applicability — declaration instead of path-glob heuristics |
| `verifyCmd` | string | verify-command fallback tier |
| `stack` | list of strings | informational (v1) |

Unknown keys are tolerated (forward-compat within a schema version).

## Precedence (most-specific-wins, resolved live)

- **strictness**: explicit per-goal setting (`create_goal(strictness=…)` /
  `set_goal_strictness`) → `strictnessDefault` → instance default (`trust`).
  A goal created WITHOUT the strictness argument is unpinned — merging a
  manifest change immediately affects it; passing the argument pins the goal.
- **verify_cmd**: planner action → goal → `verifyCmd`.

## Trust boundary (the #358/#233 class)

Every **gate-relevant** read (strictness, surface, verifyCmd) comes from the
repo's **remote default-branch tip** — the human-merged truth — never from
the worktree or the goal branch, both of which the sandboxed worker can write
to. A worker-side edit to `devclaw.json` therefore has NO effect on any gate
until a human merges it (named regression:
`tests/test_manifest_gates.py::test_manifest_edit_inside_run_does_not_change_gate_inputs`).
Workspaces with no remote (dev/stub) fall back to the worktree — there the
worktree is the only truth.

## Error posture

- **Absent** manifest → instance defaults apply (doctor warns; re-onboard
  seeds one).
- **Malformed** manifest → dispatch rejects loudly naming the parse error
  (never a silent fallback); a malformed manifest at the merged base blocks
  the affected gate read fail-closed.
