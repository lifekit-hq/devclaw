# Contract — Worker advance brief (US1)

**Where**: `devclaw/goal/tick.py` `_advance_brief` (layer 2 builds the string; the
worker in `openhands-runner` consumes it, layer 5).

## Before (today)
Orders the worker to "First read PLAN.md (create and maintain it as you go) … and
advance it by one increment."

## After (P1)
The brief orders the worker to **advance the current feature via speckit**, as
plain instructions (no vendor slash-command syntax; Principle II):

- Determine the current feature: the smallest not-yet-complete `specs/NNN-*/`
  (its `tasks.md` has unchecked items), or, if there is none and new work is
  called for, create one via `.specify/scripts/bash/create-new-feature.sh`.
- Run the speckit steps for that feature — `specify → plan → tasks → implement` —
  using the repo's `.specify/` scripts + templates. Implement the **smallest
  not-yet-done story-slice only** (one coherent slice = one reviewable PR); do
  **not** build ahead into later stories.
- Check off completed items in `tasks.md` and commit the `specs/NNN-*/` artifacts
  with the code.
- **No `PLAN.md`** is created or maintained.

## Invariants
- Prompt-text change only — **no** new host cognition call, **no** idle-path work
  (Principle III; a regression test asserts idle `FakeClaude.calls == 0`
  unchanged).
- Steering (owner input / done-gate corrections) still rides in the brief verbatim,
  as today.
- Model-agnostic: the brief references speckit as markdown/scripts, never
  Claude-Code-specific wiring (Principle II).

## Test (named regression)
`test_advance_brief_speckit.py` — the built brief instructs the speckit flow,
names `tasks.md`, and contains **no** `PLAN.md` directive; idle path adds zero
cognition calls.
