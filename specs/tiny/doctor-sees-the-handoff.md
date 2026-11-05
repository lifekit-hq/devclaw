# TinySpec: doctor reports the no-mistakes hand-off

**Branch**: goal/dc-doctor-no-mistakes-982
**Date**: 2026-11-05
**Status**: in review
**Complexity**: small (one doctor check, one test file, three doc lines)
**Issue**: #982 — doctor names a missing `claude` or `treehouse`, but not the
no-mistakes hand-off the goal layer now depends on

## North-star case

- **Failure moved**: *stopped when it shouldn't*. On 2026-11-05 goal
  dc-phone-tab-bar-971 ran a whole session and then stopped at the hand-off
  because the container had no usable no-mistakes. Nothing on the instance
  named the gap, so the only way to see it was to read a goal's exit line —
  the post-deploy check that exists for exactly this class was blind to the
  one piece of tooling added to the delivery path by #953.
- **Number that shows it**: pieces of session-critical tooling `devclaw doctor`
  covers. Before: 4 (`gh`, `git`, `claude`, `treehouse`) of 5. After: 5 of 5,
  plus the hand-off's home.
- **Cut when**: the branch stops shipping through no-mistakes (the hand-off
  moves back in-house), or doctor is replaced by a check that enumerates the
  engine's tooling structurally instead of by name.

## What

A `handoff` check in `devclaw/doctor/checks.py`, between `tools` and `pause`:

- `handoff.no-mistakes` — `ok` with the resolved path, or `fail` ("not on
  PATH") with a remedy naming `deploy/Dockerfile` and the runbook. `fail` is
  the verdict a missing `claude` / `treehouse` already gets, so
  `devclaw doctor` exits 1 on it exactly as it does for those.
- `handoff.home` — `ok` with the expanded path when `$NM_HOME` (default
  `~/.no-mistakes`) is a directory, else `warn` naming the path and the fact
  that the first goal's `no-mistakes init` creates it.
- Both skipped under `DEVCLAW_ENGINE=stub` (one `ok` finding instead), the way
  `check_tools` already skips the engine binaries: a stub engine hands nothing
  off, and a `fail` there would be a false alarm on every dev box.

## Context

| File | Role |
|---|---|
| `devclaw/doctor/checks.py` | the checks, in declared order; `check_tools` is the precedent for "a binary on PATH, `fail` when absent" |
| `devclaw/engine/claude.py` | `NO_MISTAKES` — the one name of the binary, imported rather than re-spelled |
| `devclaw/cli.py` | `_cmd_doctor`: exit 1 when any finding is `fail` or `unknown` |
| `tests/test_doctor_sees_the_handoff.py` | new — the tripwire |
| `docs/runbooks/doctor.md`, `docs/runbooks/no-mistakes-in-container.md`, `docs/reference/env-vars.md` | the three doc lines the diff makes wrong |

`NM_HOME` is not a `DEVCLAW_*` dial — no-mistakes reads it itself — so the read
stays in `checks.py`; the config doorway is for `DEVCLAW_*` only
(`tests/test_config_single_doorway.py`).

## Requirements

- `devclaw doctor` and the `doctor` tool report a `handoff.no-mistakes` line:
  found (with its path) or missing on PATH.
- A missing binary is `fail` — the same verdict as a missing `claude` or
  `treehouse`, hence the same exit code — and its remedy names
  `docs/runbooks/no-mistakes-in-container.md`.
- The line for the home reports the resolved `$NM_HOME` (or the expanded
  default) and whether it exists; a missing home is `warn`, never `fail`.
- Under the stub engine the check reports `ok` and probes nothing.
- The runbook says in one sentence that doctor now checks this.

## Rejected alternatives

- **Add `no-mistakes` to `check_tools`' binary tuple.** One line, but the home
  has nowhere to go there and every `tools.*` finding would need a remedy
  field for one of them to carry the fix. The hand-off is its own concern.
- **Check the daemon or the gate registry too** (`no-mistakes daemon status`,
  `$NM_HOME/repos`). Doctor is read-only and spawns nothing; the daemon is
  started by `_init_pipeline` before every session, so a stopped daemon is
  normal and would be a permanent `warn`. The runbook's live check covers it.
- **`fail` on a missing home.** The runbook states a fresh deploy has no home
  until the first `init`; that instance is healthy, and a check that cries
  wolf on every deploy is worse than no check.

## Tasks

- [x] `check_handoff` + registered in `CHECKS`
- [x] `tests/test_doctor_sees_the_handoff.py` — found / missing on a stubbed
      PATH, home present / absent / defaulted, the stub engine, and the
      verdict → exit-code mapping
- [x] `docs/runbooks/doctor.md`, `docs/runbooks/no-mistakes-in-container.md`,
      `docs/reference/env-vars.md` + their `docs/INDEX.md` currency tags

## Done When

- [x] `devclaw doctor` on this host shows the two hand-off lines
- [x] `.devclaw/verify` green
- [ ] This PR's CI green
