# Quickstart — Validating the Speckit Execution Substrate (P1 MVP)

How to prove US1 + US2 work. **Tier A** is the stubbed suite (no docker/claude);
**Tier B** is the live-shakedown that is the real "done" lock (#538).

## Prerequisites
```bash
pip install -e ".[dev]"
# In a worktree, verify the import path FIRST (rules/testing.md):
.venv/bin/python -c "import devclaw; print(devclaw.__file__)"   # must print the WORKTREE path
```

## Tier A — stubbed regression suite (code-done)
```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q \
  tests/test_slice_guard_tasks.py \
  tests/test_onboard_speckit.py \
  tests/test_advance_brief_speckit.py
# then the full suite (must stay ≥ baseline, no idle-token regressions):
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q
```

**Expected (maps to Success Criteria):**
- `test_advance_brief_speckit` — the advance brief runs the speckit flow, names
  `tasks.md`, contains **no** `PLAN.md` directive; idle path adds 0 cognition calls.
- `test_slice_guard_tasks` — build-ahead detected from `specs/*/tasks.md` checkbox
  flips; `PLAN.md` **never read** (SC-003); legacy fallback + neither-present cases
  hold.
- `test_onboard_speckit` — `.specify/` present → adopt, no `PLAN.md` (SC-001);
  bare repo → reviewable PR, 0 silent commits (SC-004); open install PR blocks
  feature work.
- Zero-token guard tests (`FakeClaude.calls == 0`) still green (Principle III).

## Tier B — live-shakedown (the real lock, #538)
Run the real pipeline (logged-in `claude` + docker) per
`docs/runbooks/live-shakedown.md`, companion mode:

1. **Adopt path**: point devclaw at a repo that already has `.specify/`. Dispatch a
   feature issue. Confirm:
   - a `specs/NNN-*/` (spec → plan → tasks) set is produced **in-sandbox**,
   - the increment is one story-slice = one PR,
   - **you merge** the PR (human backstop),
   - the done-gate closes the issue on `achieved` grounding on `spec.md`,
   - **no `PLAN.md`** was written or read anywhere in the run.
2. **Install path**: point devclaw at a bare repo (no `.specify/`). Confirm a
   **reviewable install PR** appears (nothing silently committed), and feature work
   waits until it is merged.

**Passing Tier B is the gate** that unlocks the shrink slice (#539) — only then do
we remove the host `investigating→firming→decompose` chain.

## US3 — label-routed ceremony tiers (P2 increment, added 2026-08-18)

**Tier A (stubbed):**
```bash
TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q \
  tests/test_tier_routing.py \
  tests/test_advance_brief_tiers.py
```
- routing table holds for every signal row; ambiguity/conflict routes to full,
  never lighter (monotone property);
- per-tier brief blocks present; the `direct` brief forbids artifact creation
  (presence AND absence asserted); idle path adds 0 cognition calls;
- vendored scripts carry the `SPECKIT_NO_BRANCH` guard (vendor-integrity test).

**Tier B (live):** dispatch three real issues against a speckit repo —
1. `feature`-labeled → full `specs/NNN-*/` set, one slice per PR;
2. `bug`-labeled → `specs/bugfix-NNN-*/` set with the regression test committed
   **before** the fix, no full feature spec (SC-005);
3. `docs`-labeled → direct fix PR, **zero** artifact dirs created (SC-005);
and confirm delivery stays on the goal branch in all three (no `bugfix/NNN-*`
branch from the vendored scripts).

## Out of scope for this validation
- US4 PLAN.md migration of existing repos (P2).
- 007 autonomous dispatch (flag stays OFF).
- `modify`/`refactor`/`deprecate` workflows from the extension pack (not vendored).
