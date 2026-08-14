# Quickstart — validating P1 (project reference key + preflight)

How to prove the P1 slice works end to end. Run in the worktree; verify the
import path first (`.claude/rules/testing.md`).

## Prerequisites

```bash
.venv/bin/python -c "import devclaw; print(devclaw.__file__)"   # MUST print the worktree path
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q               # green baseline BEFORE changes
```

## Validation scenarios (each becomes a named regression test)

### 1. Dispatch by key resolves server-side
- Register a project whose row has a real git workspace + `lifekit-hq` repo.
- `dispatch_task(kind=…, project_id=<id>, goal=…)` with NO workspace path.
- **Expect**: task runs in the resolved workspace; on delivery the PR targets the
  resolved repo. (`test_dispatch_by_project_id_resolves_workspace_and_repo`)

### 2. Unknown project rejected synchronously, zero work
- `dispatch_task(project_id="ghost", …)`.
- **Expect**: `ToolError("unknown project_id: 'ghost' …")`; **no** task row
  created; `FakeClaude.calls == 0` and `FakeEngine` never launched.
  (`test_dispatch_unknown_project_id_rejected_zero_token`)

### 3. Missing / non-git workspace caught at admission (not launch)
- Register a project pointing at a path with no `.git`.
- Dispatch.
- **Expect (P1)**: loud reject at admission with the path + reason; the task is
  **never** a claimed `running` row; `sandcastle` is never reached. Contrast the
  old failure (`sandcastle.py:302`, post-claim). (`test_preflight_rejects_non_git_workspace_before_claim`)

### 4. Override knobs unchanged by-key vs by-path
- Register a project with `automerge=on`, `review_gate=off`.
- Dispatch by key; drive a settle.
- **Expect**: identical merge/gate decisions as the pre-change path-keyed
  resolution (the knobs still resolve via `find_by_workspace_dir` off the
  resolved `workspace_dir`). (`test_by_key_dispatch_preserves_override_knobs`)

### 5. Write-time path validation
- `update_project` with a non-canonical / host-shaped path.
- **Expect**: normalized (or rejected with a reason); a well-formed path
  round-trips unchanged. (`test_register_project_validates_and_normalizes_workspace_path`)

### 6. Zero-token guard (load-bearing)
- Idle tick + a by-key dispatch + a rejected unknown id + a preflight rejection.
- **Expect**: `FakeClaude.calls == 0` throughout.
  (`test_dispatch_resolution_and_preflight_stay_zero_token`)

## Migration check (hard cutover)

```bash
# After migration, NO caller-facing workspace_dir param should remain on the dispatch tools:
grep -nE 'def (dispatch_task|implement_feature|fix_bug|review_repository|onboard|create_goal|start_program)\b' -A6 devclaw/server/tools.py | grep workspace_dir   # expect: no matches
# Full suite green at same-or-higher count than the pre-change baseline:
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q
```

## Done = P1 complete when

- All six scenarios pass as named tests.
- Full suite green (no lower than baseline).
- `grep` shows no `workspace_dir` caller param on the dispatch tools.
- PR body states the exact count of files touched by the migration (Constitution VI).
- The waiter-prompt lockstep step is called out in the PR body as a required
  co-release (Denys/OpenClaw), NOT silently assumed.
