#!/usr/bin/env bash
# post-run hook — invoked by runner.py after the agent finishes, BEFORE the
# verify gate.
#
# Args: $1 workspace_dir   $2 kind   $3 task_id   $4 verify_cmd (may be empty)
#
# Best-effort mechanical checks. Writes warnings to stdout (the runner captures
# them and attaches to the result). Failures here do NOT abort — the verify
# gate is the source of truth for go/no-go.
#
# This hook USED to answer "what did the agent change?" itself, with
# `git diff <pre_head>` against a `.devclaw-pre-head` sidecar. That was a third
# independent computation of the change, and it had the same blind spot as the
# gates: a file the agent never recorded was invisible to it, so a run that had
# just CREATED AGENTS.md reported it as untouched (#630). Spec 013 gives the
# host one materialized answer, and the three checks that lived here now read
# it — see devclaw/quality/change_advisories.py. They were relocated, not
# copied: teaching a second component the same trick is how the two views drift
# apart again.
#
# The hook itself stays as the per-repo composition point: runner.py still runs
# <workspace>/.agent/hooks/post-run.sh alongside this one.

set -u

exit 0
