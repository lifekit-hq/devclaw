# Quickstart Validation: Unattended-Week Operation

Prerequisites: `pip install -e ".[dev]"`; the suite is fully stubbed — no
docker, no `claude`, no `gh` (all faked). Live legs run on the VPS after
deploy (see the end).

## US1 — merge-on-close

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q tests/test_merge_on_close.py tests/test_goal_tick.py
```

Expected named tests (all green):

- `test_achieved_close_squash_merges_the_cumulative_pr_before_done` —
  fake gh records `pr merge --squash` BEFORE the ACHIEVE transition; goal
  ends `done`; ping text carries the merged sha.
- `test_merge_conflict_dispatches_one_resolution_increment_then_parks` —
  first CONFLICT → one `implement_feature` dispatch with the conflict brief;
  second CONFLICT → `blocked` / `mechanical:merge_failed`; never a third.
- `test_already_merged_pr_at_close_is_success` (FR-004).
- `test_closed_unmerged_pr_parks_loudly` (FR-004).
- `test_resume_after_merge_failure_retries_merge_without_done_gate` —
  `FakeClaude.calls == 0` on the resume path (FR-003).
- `test_blocked_goal_releases_project_lane_for_queued_successor` (FR-015).
- Zero-token guards: existing `FakeClaude.calls == 0` tests untouched.

## US2 — self-deploy

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q tests/test_self_deploy_trigger.py tests/test_deploy_compose.py
```

- `test_devclaw_repo_merge_records_deploy_pending_and_waits_for_quiescence`
  — trigger fires only at `count_running() == 0`; a running task defers it.
- `test_deploy_pending_expires_loudly_after_bounded_wait`.
- `test_non_devclaw_merge_never_triggers_deploy` (deploy pin holds).
- Script check (no docker needed):
  `bash -n deploy/deploy-devclaw-auto.sh` + the compose-drift tests.

## US3 — quiet mode

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q tests/test_quiet_mode.py tests/test_cycle_report.py
```

- `test_quiet_mode_suppresses_and_records_all_noncritical_pings` — one event
  per ping class incl. the cycle report (the service.py:510 direct-send
  path); only `send_critical` reaches the wire.
- `test_auth_pause_ping_pierces_quiet_mode`.
- `test_quiet_mode_expiry_self_disarms`.
- `test_suppressed_backlog_reads_back_in_order` (FR-014).

## Full gate (every PR)

```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q && ruff check . && mypy
```

## Live legs (VPS, after deploy — the SC-001/SC-002 proof)

1. Arm quiet mode with a short expiry: `set_quiet_mode(on=true, until=<+1h>)`.
2. File one tiny throwaway goal (shakedown-bench class) → watch it close AND
   merge with zero touches (`tail_goal` shows "merged <sha>").
3. Confirm the successor test: file two queued goals; first merges; second's
   workspace starts from post-merge head.
4. Trigger one devclaw self-deploy against a no-op merge; watch
   `/health` git_sha flip; then force a probe failure on a scratch tag to
   watch one rollback.
