# tinyspec: console_dist resolves one directory too deep

## What
`/console` on a deployed instance 503s "console bundle not built" even though
the image builds and ships the bundle.

## Context
#625 moved the console routes from `devclaw/server/http.py` into
`devclaw/server/routes/console.py` but kept the bundle path expression
`Path(__file__).resolve().parent / "console_dist"` — which now resolves to
`devclaw/server/routes/console_dist`. The bundle ships at
`devclaw/server/console_dist` (built by deploy/Dockerfile, force-included by
pyproject `[tool.hatch.build.targets.wheel].artifacts`). Broken on every
deploy since #625 (ceffdba, 2026-08-23); found live 2026-08-24 when the
operator opened the console.

## Requirements
- `/console` serves the shipped bundle again.
- The route's path and pyproject's wheel-artifacts glob cannot drift apart
  silently again.

## Plan
One-line fix: anchor to the server package (`parents[1]`). One regression
test pinning the route's path to the exact location pyproject ships.

## Tasks
- [x] Fix `_CONSOLE_DIST` in `devclaw/server/routes/console.py`
- [x] `tests/test_console_dist_path.py` — path ↔ pyproject artifacts parity

## Done-When
Suite green incl. the new parity test; deployed `/console` returns the SPA.
