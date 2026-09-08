# TinySpec: the suite owns its environment — a hermeticity pin is never a default

**Issue**: none (Denys, 2026-09-08 night — "Fix the red CI")
**Branch**: fix/suite-owns-its-environment
**Date**: 2026-09-08
**Status**: done — implemented 2026-09-08 (suite 1526 passed / 5 skipped at -n auto AND -n0; ruff, mypy, lint-imports green)
**Complexity**: small

## North-star case

- **Failure moved**: ran but needed the owner. Since 2026-09-08 17:45 UTC every
  CI run on `main` has been red — 82f82ca, f30f61d and PR #879 — on an
  `sqlite3.OperationalError: database is locked` raised while *collecting*
  `tests/test_auth_middleware.py`. Spec 032 makes the project's CI rollup the
  verdict of record: devclaw reads its own repo's rollup before a done-check and
  before merge-on-close, so a permanently-red `main` turns every devclaw-repo
  goal into a `mechanical:ci` hold the owner has to reason about by hand.
- **Number that shows it**: CI on `main` green again, and the pytest job's
  variance gone — the failure is load-dependent, so the check is 3 consecutive
  green runs on the loaded VPS runner, not one.
- **Cut when**: never — this is a hermeticity invariant of the tripwire net
  itself. A net that cannot run is worse than no net.

## Root cause

`tests/conftest.py` pins the suite's environment with `os.environ.setdefault`.
Two of those pins are load-bearing hermeticity guards, and `setdefault` is the
wrong verb for both:

1. **The value is inherited, so the pin is a no-op.** Under `-n auto` the xdist
   CONTROLLER imports `conftest` first, computes one `DEVCLAW_DB` path, and
   execnet spawns every worker with the controller's `os.environ`. Each worker's
   own `setdefault` then sees the value already set and does nothing. The
   per-worker `PYTEST_XDIST_WORKER` suffix the comment describes is dead code —
   the path every worker actually opens is the controller's, suffixed `main`:

   ```
   gw0 /tmp/…/devclaw-suite-s6qdwe4w/devclaw-main.db      # not devclaw-gw0.db
   ```

   So all 16 workers share ONE SQLite file. `devclaw/server/_state.py:43` builds
   a real `StateStore` **at import time**, so merely importing `devclaw.server`
   runs `PRAGMA journal_mode = WAL` against it. Sixteen processes racing that
   pragma is a lock contention the fast local box wins and the loaded VPS runner
   — the same host the live instance runs on — loses. The losing worker fails to
   import the module, its 3 tests vanish from that worker's collection, and
   xdist aborts the run on the collection mismatch. That is why the break is
   load-dependent and tracked the finance-sentry lane getting busy, not any commit.

2. **An ambient value silently wins.** CI runs on the deploy host. Any
   `DEVCLAW_DB` or `DEVCLAW_SKILLS_DIR` in the runner's environment would be
   adopted by `setdefault` — pointing the suite at a real database, or at the
   baked `/opt/devclaw/skills/` bundle whose content changes independently of the
   branch (the #610 wrong-copy bug the `DEVCLAW_SKILLS_DIR` pin exists to
   prevent). The line immediately below them already learned this lesson and
   states it: `DEVCLAW_SELF_REPO` is `pop`'d, "cleared, never defaulted", because
   "CI runs on the same host devclaw is deployed to".

The class: **a hermeticity pin is an assertion about the suite's world, not a
default for it.** `setdefault` makes it negotiable by whoever set the variable
first — an inheriting parent process or an ambient host.

Rejected: raising SQLite's busy timeout (treats the symptom — the workers still
share one database); dropping `-n auto` (hides the race and costs ~20s a run);
making `_state.store` lazy (the right fix for the *import-time* side effect, but
that is layer-1 surgery and a separate concern — filed below, not done here).

## What

Both hermeticity pins in `tests/conftest.py` become unconditional assignments.
`mkdtemp` already yields a unique directory per process, so each worker gets its
own database once the value is no longer inherited; the `PYTEST_XDIST_WORKER`
suffix stays as legible belt-and-braces.

## Context

| File | Role |
|------|------|
| `tests/conftest.py` | Modified — `DEVCLAW_DB` and `DEVCLAW_SKILLS_DIR` pins set unconditionally |
| `tests/test_suite_env_hermeticity.py` | New — the invariant as a tripwire: the pins are per-process and survive a hostile ambient environment |

## Requirements

1. Every xdist worker opens a `DEVCLAW_DB` no other worker opens.
2. An ambient `DEVCLAW_DB` / `DEVCLAW_SKILLS_DIR` in the environment does not
   reach the suite.
3. `DEVCLAW_SKILLS_DIR` always resolves to the in-repo `runner/skills/`.
4. The suite is green under `-n auto` and under `-n0`.

## Plan

Change the verb on both pins; pin the invariant with a guard test that fails if
either pin becomes negotiable again.

## Tasks

- [x] `tests/conftest.py`: both pins unconditional
- [x] `tests/test_suite_env_hermeticity.py`: per-process DB + hostile-ambient guard
- [x] Full suite + `ruff check .` + `mypy` + `lint-imports` green
- [ ] 3 consecutive green CI runs on the VPS runner (verified after merge — the race only shows under runner load)

## Done when

- CI on `main` is green three runs running.
- A `DEVCLAW_DB=/tmp/hostile.db pytest -q` run still uses the suite's own path.

## Follow-up filed, not fixed here

`devclaw/server/_state.py:43` constructs `StateStore(DB_PATH)` (and
`ProjectRegistry(DB_PATH)`) at IMPORT time, so importing `devclaw.server`
creates and WAL-locks a database as a side effect. This tinyspec makes the
suite safe from it; the import-time global itself is a layer-1 design finding
and needs its own artifact.
