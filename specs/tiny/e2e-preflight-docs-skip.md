# TinySpec: tell the session what the host cannot run, and skip the browser e2e for a docs-only diff

**Branch**: fm/dc-987-e2e-preflight
**Date**: 2026-11-06
**Status**: delivered
**Complexity**: small (two functions and one env var in `engine/claude.py`, one skill paragraph, one test file, two doc lines)
**Issue**: #987 — the done-gate e2e step blocks on a worker host without browser libraries, after a long paid session

## North-star case

- **Failure moved**: *stopped when it shouldn't*, and its cost. A docs-only
  goal for lifekit-dashboard ran one session of about 95 minutes (roughly 176M
  cache-read tokens) and ended BLOCKED because the e2e step needed
  `libXfixes.so.3`, which only root can install. Two faults: the gap was found
  at the end of the work, and a documentation diff needed the browser at all.
- **Number that shows it**: paid session time between the start of a session
  and the first mention of a missing browser library: 95 minutes before, the
  first turn after. Docs-only branches blocked by the browser e2e: 1 before, 0
  after (given a repository script that honours the signal).
- **Cut when**: the worker host ships the browser libraries (the preflight then
  never fires), or the engine stops running a repository script for verify.

## What

1. **Preflight** — `browser_preflight` in `engine/claude.py`. For a fresh code
   session on a repository that drives a browser (`playwright.config.*` at the
   root or one level down, or `playwright` in `.devclaw/verify`), `ldd` the
   Playwright Chromium on the host (`PLAYWRIGHT_BROWSERS_PATH`, else
   `~/.cache/ms-playwright`). A library `ldd` reports `not found` becomes one
   `Host preflight:` paragraph at the top of the prompt and a `Preflight`
   event. Facts only; the session's contract decides whether it can proceed,
   and one skill line tells it to end BLOCKED early when it cannot. No browser
   installed, no `ldd`, or a clean `ldd` is silent.
2. **Docs-only signal** — `docs_only_diff`: everything changed against the
   merge-base with `origin/HEAD|main|master` (commits, edits, new files) is
   documentation (`is_doc_path`: a doc-format file at the root, under
   `docs/`, `doc/`, `specs/`, `.devclaw/`, or a README/CHANGELOG/LICENSE-style
   name anywhere). `run_verify` then sets `DEVCLAW_DOCS_ONLY=1` for the script.
   A missing base, an empty diff or any other path fails closed.

## Requirements

- The engine decides the facts (software owns the verdict inputs); the
  repository's own `.devclaw/verify` decides what to leave out. The skill
  states that only the browser step may be skipped.
- Not changed: the worker image, the container, `DEVCLAW_SKIP_E2E`, spec 100 —
  those are owner decisions.

## Done when

- A browser repository on a host whose Chromium misses a library gets the
  `Host preflight:` paragraph in its first prompt (`tests/test_host_facts_before_the_gate.py`).
- A documentation-only diff runs verify with `DEVCLAW_DOCS_ONLY=1`; a mixed,
  empty or base-less diff does not (same file).
- `pytest`, `ruff`, `mypy`, `lint-imports` green.
