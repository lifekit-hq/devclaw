#!/usr/bin/env bash
# pre-run hook — invoked by runner.py before the agent starts.
#
# Args: $1 workspace_dir   $2 kind   $3 task_id
#
# Best-effort. Failures here surface as warnings in the runner result but do
# NOT abort the run. Keep checks fast (< 1s total).

set -u

workspace_dir="${1:-}"
kind="${2:-implement_feature}"

# Sanity: workspace must exist and be writable for code-writing kinds.
if [ ! -d "$workspace_dir" ]; then
  echo "warn: workspace_dir does not exist: $workspace_dir"
fi
if [ "$kind" = "implement_feature" ] || [ "$kind" = "fix_bug" ] || [ "$kind" = "onboard" ]; then
  if [ ! -w "$workspace_dir" ]; then
    echo "warn: workspace_dir is not writable: $workspace_dir"
  fi
fi

# No HEAD snapshot is written here. The pre-run reference is the host's
# (`tasks.pre_run_sha`), and since spec 013 the host also materializes the
# post-run reference, so the worker layer no longer needs — or is allowed — its
# own copy of where the change starts. One definition of the change, one owner.

exit 0
