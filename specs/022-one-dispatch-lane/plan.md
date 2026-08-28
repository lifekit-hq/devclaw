# Implementation Plan: One Dispatch Lane (spec 022)

**Branch**: `goal/devclaw-022-one-lane-2026-08-27` | **Date**: 2026-08-27 | **Spec**: [spec.md](./spec.md)

## Summary

Replace two dispatch lanes (goal lane + identity-less direct lane) with one: every
mutating companion dispatch creates-or-attaches a `one_shot` goal keyed to a
(project, issue) identity enforced at the SQLite level. Three user stories:

- **US1 (P1)**: Issue-keyed dispatch with SQLite-enforced uniqueness and create-or-attach
- **US2 (P2)**: Repeal single-writer exemption; companion dispatches ride the full goal lane
- **US3 (P3)**: Retire freeform-prose path + demolish program/DAG machinery

## Technical Context

**Language**: Python 3.11 | **Framework**: FastMCP + SQLite (devclaw.db)

**New table**: `goal_issue_identity (project_id TEXT NOT NULL, issue_key TEXT NOT NULL,
goal_id TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY(project_id, issue_key))`

PRIMARY KEY implies NOT NULL on both columns — the spec 012 lesson (nullable ref silently
disables its own constraint).

**Layer touch points** (spec 022 US1):
- `devclaw/goal/state.py` — `_bootstrap()`: add `goal_issue_identity` table
- `devclaw/goal/state_content.py` — `GoalStateContentMixin`: raw SQLite identity methods
- `devclaw/goal/store/content.py` — `GoalContentMixin`: wrapper methods
- `devclaw/goal/service.py` — `GoalService.dispatch_issue()`: async create-or-attach
- `devclaw/server/tools/tasks.py` — `dispatch_task`, `implement_feature`, `fix_bug`: `issue_ref` param
- `devclaw/doctor/checks_instance.py` — `check_goal_issue_identity_table`
- `tests/test_issue_keyed_dispatch.py` — named regression tests

**Layer touch points** (spec 022 US2 — next increment):
- `devclaw/server/tools/tasks.py` — repeal `_project_hold_warning` (replace with hard block)
- `devclaw/goal/service.py` — workspace-prep-to-head hook in `dispatch_issue()`

**Layer touch points** (spec 022 US3 — final increment):
- `devclaw/server/tools/tasks.py` — `dispatch_task` requires `issue_ref`; prose auto-files
- `devclaw/queue/programs.py` — delete DAG machinery
- `tests/` — delete spec's tests-that-die inventory

## Judgment calls

- **issue_key format**: `str(issue_number)` relative to the project's registered repo.
  The `project_id` disambiguates the repository. Cross-repo refs remain unsupported
  (per the spec assumptions — same-repo only).
- **issue_ref type in MCP tool**: `Optional[int]` (a plain issue number). Cross-repo
  references ("owner/repo#N") deferred until FR-001's "one repo per project" assumption
  no longer holds.
- **Re-arm CAS**: `UPDATE ... WHERE goal_id=<old>` ensures exactly one concurrent re-arm
  wins; the losing caller re-reads the identity and returns "attached".
- **`create_goal()` (sync) called from `dispatch_issue()`**: The live issue fetch happens
  ONCE in `dispatch_issue()` (for state check); `create_goal(sync)` skips the network call
  but validates format and issue overlap. The done-gate fetches the issue again at
  evaluation time (existing behavior — freshness by construction).
- **Saga slots for companion dispatch**: `out_of_scope=[]`, `invariants=[]`,
  `established=[]` (all explicitly empty). The issue IS the spec (spec 024 direction);
  empty lists are a deliberate declaration, not an omission.
