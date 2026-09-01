# ci-tmpdir-leak — CI's pytest TMPDIR leaks onto the runner host's tmpfs

## What

The CI `test` job creates its private pytest TMPDIR with a bare
`mktemp -d`, which lands in the runner host's `/tmp`. On the lifekit VPS
`/tmp` is a tmpfs, and the ~148MB of suite debris per run is never
removed — tmpfs pages count against MemAvailable, so accumulated runs
starve the host until devclaw's memory admission brake
(`task-queue: dispatch held — host MemAvailable … < floor`) stops
launching sandboxes.

## Context

Found 2026-09-01: ~25 leaked `tmp.*` dirs (~3.7GB) plus
`/tmp/pytest-of-lifekit` (438MB) had pushed MemAvailable to ~5.9GB,
below the 7680MB floor — both in-flight goals sat `pending` for half an
hour with an empty trace. The private-TMPDIR pattern itself is
load-bearing (root-owned `/tmp/pytest-of-*` crashes `tmp_path`
fixtures — see `.claude/rules/testing.md`); only its placement is wrong.

## Requirements

- The suite still runs with a private, per-run TMPDIR (the root-owned
  `/tmp/pytest-of-*` hazard stays fenced).
- The TMPDIR is placed where the runner reclaims it and it does not
  consume host tmpfs.

## Plan

Stage the TMPDIR under `$RUNNER_TEMP` (`mktemp -d -p "$RUNNER_TEMP"`):
disk-backed, and the Actions runner wipes `_work/_temp` between jobs —
same placement the job's venv already uses.

## Tasks

- [x] `.github/workflows/ci.yml`: `mktemp -d` → `mktemp -d -p "$RUNNER_TEMP"`.

## Done when

- The `test` job's TMPDIR is created under `$RUNNER_TEMP`, not `/tmp`.
- A CI run leaves no new `tmp.*` dir on the runner host's `/tmp`.
