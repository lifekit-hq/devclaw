# TinySpec: doctor reports the no-mistakes hand-off

**Branch**: goal/dc-doctor-no-mistakes-982
**Date**: 2026-11-05
**Status**: in review (self-review round 2: the home's unreadable and uncreatable states were landing in the self-healing `warn`; round 3: `expanduser` on the operator's value cleared a tilde home no-mistakes resolves elsewhere)
**Complexity**: small (one doctor check, one test file, four doc lines, and `.devclaw/verify`'s bootstrap — see Context)
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
- **Which half is loud, and what has actually been seen** — stated plainly,
  because the number above flatters this change: NEITHER `fail` branch has an
  observed instance on this deployment. The binary ships in the image
  (`NO_MISTAKES_VERSION`), and what goal dc-phone-tab-bar-971 hit was the
  home — emptied on every redeploy because it sat on the container's writable
  layer (#981) — which `_handoff_home` reports as `warn`, since the engine's
  pre-session `no-mistakes init` now recreates it. So both `fail` branches are
  forward guards against states no deploy has produced, and today's real
  verdict on a healthy host is two `ok` lines. What the check buys is that the
  instance answers "can this host ship a branch?" on demand, before a goal has
  to answer it one exit line at a time.
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
  `~/.no-mistakes`) is a directory; `warn` naming the path and the fact that
  the first goal's `no-mistakes init` creates it when nothing is there yet;
  `fail` for every home no session heals — a path that does not resolve
  absolute, a file or dangling symlink on it, an unwritable parent (`mkdir -p`
  semantics: the nearest existing ancestor must be a writable directory), or a
  home `os.stat` cannot read. `$NM_HOME` is judged as the TOOL receives it:
  the engine hands no-mistakes the raw environment (`host_env`), so stripping
  or expanding the operator's value would clear a host that cannot ship. The
  tilde is the case that matters — no-mistakes does not expand one either (it
  makes a literal `~` directory in its cwd), and the documented default is
  written with one, so `NM_HOME: ~/.no-mistakes` in a compose block is a
  relative home. Only the default is expanded, and only when the var is unset.
- Both skipped under `DEVCLAW_ENGINE=stub` (one `ok` finding instead), the way
  `check_tools` already skips the engine binaries: a stub engine hands nothing
  off, and a `fail` there would be a false alarm on every dev box.

## Context

| File | Role |
|---|---|
| `devclaw/doctor/checks.py` | the checks, in declared order; `check_tools` is the precedent for "a binary on PATH, `fail` when absent" |
| `devclaw/engine/claude.py` | `NO_MISTAKES` — the one name of the binary, imported rather than re-spelled |
| `devclaw/cli.py` | `_cmd_doctor`: exit 1 when any finding is `fail` or `unknown` |
| `tests/test_doctor_seeded_faults.py` | the tripwire — the "doctor seeded faults" class had no test since the v2 rewrite deleted `tests/test_doctor.py`, so it is named and parametrized after the CLASS (every binary on the delivery path), not after this instance |
| `.devclaw/verify` | carried in this increment, not by #982: inside the devclaw container the script could not run at all (the image installs runtime deps only, so `python3 -m pytest` died on line one), which is a verify that lies. It now bootstraps the declared deps into a worktree-local `.venv` — the shape `.no-mistakes.yaml`'s `prepare` already uses — probing by distribution name so a dep added later is installed rather than skipped, topping up an interpreter that runs, and replacing a tree that has none (which otherwise killed the script under `set -e` before the first gate). The trade-off, written down: the first round on a fresh worktree needs the package index, so an index outage reads as a red verify (`INTERRUPTED`) rather than a stop on a fact. Accepted because the alternative is a script that cannot execute; every later round is a probe and a PATH prepend. |
| `docs/runbooks/doctor.md`, `docs/runbooks/no-mistakes-in-container.md`, `docs/runbooks/live-shakedown.md`, `docs/reference/env-vars.md` | the four doc lines the diff makes wrong |

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
  default) and its state: a home that is not there yet AND creatable is
  `warn`, never `fail`; every home no session heals is `fail` — uncreatable,
  unreadable, or not a directory. A state the checker cannot observe must
  never fall into the self-healing `warn`: `os.path.isdir` and
  `os.path.lexists` both swallow `EACCES`, which is why the home is stat'd
  explicitly.
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
  wolf on every deploy is worse than no check. The split above is the answer:
  `warn` for the home `init` creates, `fail` for the one it cannot.
- **Lean on a mechanism already at this boundary instead.** Two exist, and
  neither reports to the instance: no-mistakes ships its own
  `no-mistakes doctor` (it needs the binary that may be missing, and says
  nothing about devclaw's wiring), and since #981 the engine pre-flights
  `no-mistakes init` before the first turn, so a pipeline that cannot start
  spends no session — but it is read by the goal that hit it, one exit line at
  a time, never by a post-deploy check. This adds the third observer of the
  same fact on purpose: it is the only one a human runs BEFORE a goal does.

## Tasks

- [x] `check_handoff` + registered in `CHECKS`, with the home's three states
      (`ok` / `warn` not-yet-created-and-creatable / `fail` everything a
      session cannot heal) in `_handoff_home` + `_creatable`
- [x] `tests/test_doctor_seeded_faults.py` — the missing-binary sweep
      parametrized over all five, the home's states (present / absent /
      defaulted / symlinked / a file on the path / relative / padded /
      unwritable parent / unreadable), the stub engine, registration in
      `CHECKS`, and the exit code end to end plus the verdict → code mapping
- [x] `docs/runbooks/doctor.md`, `docs/runbooks/no-mistakes-in-container.md`, `docs/runbooks/live-shakedown.md`,
      `docs/reference/env-vars.md` + their `docs/INDEX.md` currency tags

## Done When

- [x] `devclaw doctor` on this host shows the two hand-off lines
- [x] `.devclaw/verify` green
- [ ] This PR's CI green
