# Spec 027 — Stale worker inputs

**Issue:** lifekit-hq/devclaw#592

## What

A worker can be dispatched against stale inputs on two independent axes:

1. **Stale workspace** — The workspace clone may be behind origin when the engine
   starts. The goal-dispatch path calls `prepare_workspace` before each action and
   is already correct. The *direct dispatch* path (`dispatch_task` with no
   `target_branch` / `base_branch` / `parent_goal_id`) has NO workspace reset
   and runs the engine in whatever state the workspace was last left in — often a
   stale feature branch from a prior task.

2. **Stale issue reasoning** — An issue graded `devclaw-ready` may describe a
   condition that no longer holds (e.g. "fix bug X" when X is already fixed on
   main). The intake readiness gate judges groundability — surface / change /
   intent — but does not check whether the DESCRIBED CONDITION still exists. An
   issue whose fix is already present can pass grading and trigger a full session
   that produces work nobody asked for.

Both happened in the 2026-08-22 incident (devclaw-usage-endpoint-2026-08-22):
the worker produced a commit titled "fix #581" for a bug already fixed on main
three times, the delivery was rejected by origin, and devclaw auto-retried with
a second full session.

## Requirements

### US1 — Direct dispatch workspace reset (P1, mechanical)

- **FR-001** When `dispatch_task` is called with no `target_branch`, no
  `base_branch`, and no `parent_goal_id` (direct dispatch), `_execute` calls
  `prepare_workspace(workspace_dir, branch=None)` to reset to `origin/<default>`
  before the engine runs.
- **FR-002** Goal-path tasks (`parent_goal_id` set) skip this reset — the goal
  tick already ran `prepare_workspace` before dispatch; re-prepping would reset
  the goal branch.
- **FR-003** Pause-resume tasks skip this reset — the workspace survives the
  requeue untouched by contract.
- **FR-004** Prep failure for direct dispatch is best-effort + loud: a
  `WorkspaceError` or generic exception is logged to stderr and the task
  continues. Local-only workspaces (tests, local checkouts) degrade gracefully
  rather than blocking.
- **FR-005** The zero-token idle guard is untouched: no LLM calls on any path.

### US2 — Issue staleness grading (P2, cognition at grading time)

- **FR-006** The intake readiness gate adds a staleness axis: it checks whether
  the described condition still holds in the repository, using the repo context
  already passed to the grader.
- **FR-007** An issue whose described fix is already present in the repo grading
  `not ready` with a concrete reason: "the described condition appears to be
  already resolved in the repository."
- **FR-008** Staleness is a GRADING-TIME cognition call — never on a tick path.
  The zero-token idle guard is untouched.
- **FR-009** A sizing disagreement never moves the staleness verdict; they are
  independent axes on the same one-shot call.
- **FR-010** The `ReadinessVerdict` exposes a `stale` bool field; the intake
  orchestrator surfaces it in the label comment and the console.

## Done when

- FR-001–FR-005: A direct dispatch task (no branch params, no parent_goal_id)
  starts the engine on the default branch head; goal-path tasks are byte-unaffected;
  named regression test `test_direct_dispatch_without_target_branch_resets_to_origin_head`
  passes.
- FR-006–FR-010: An issue whose described condition is already resolved in the
  repo grades `not ready` with a staleness reason; named regression test
  `test_stale_issue_graded_not_ready_when_condition_already_resolved` passes.
- The zero-token idle guard tests (`FakeClaude.calls == 0`) stay green.
- Full pytest suite, ruff, and mypy are green.

## Out of scope

- Fetching from origin in the goal-tick path (already handled).
- Automatic re-grading of existing `devclaw-ready` issues.
- Detecting staleness for prose goals (only issue-backed goals have a "described
  condition" that can be grounded against the repo).

## Rejected alternatives

- **Block direct dispatch on prep failure** — local-only workspaces (tests,
  local checkouts) don't have an origin remote; blocking would break the test
  suite and dev ergonomics. Best-effort matches the pre-existing goal-branch path
  on a hiccup.
- **Staleness check on the tick path** — forbidden by the zero-token idle guard;
  cognition must only fire at grading time.
