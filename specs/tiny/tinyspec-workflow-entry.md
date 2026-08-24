# TinySpec: Register the tiny lane in the workflow registry

**Branch**: feat/tinyspec-workflow-entry
**Date**: 2026-08-24
**Status**: done
**Complexity**: small

## What

The workflow registry (`.specify/workflows/workflow-registry.json`) lists only
"Full SDD Cycle", while the tinyspec lane adopted in #669 exists only as an
extension — so the one file meant to answer "how does work run here" shows one
lane of two. Add a local `tinyspec` workflow chaining
`speckit.tinyspec → human review gate → speckit.tinyspec.implement`, registered
through the CLI's own mechanism (`specify workflow add <local path>`), never by
hand-editing the registry.

## Context

| File | Role |
|------|------|
| `.specify/workflows/tinyspec/workflow.yml` | New — the lane definition (steps + gate), modeled on `.specify/workflows/speckit/workflow.yml` |
| `.specify/workflows/workflow-registry.json` | Modified — by `specify workflow add`, not by hand |
| `.claude/rules/speckit-workflow.md` | Modified — "Below spec size" section names the registered workflow id |

## Requirements

1. `specify workflow list` shows both `speckit` and `tinyspec` as installed.
2. The tinyspec workflow's steps are exactly: generate tiny spec → human gate
   (approve/reject, reject aborts) → implement. No step invokes the full
   pipeline's commands.
3. The registry entry is written by the CLI (`source` reflects a local install),
   preserving the existing `speckit` entry byte-for-byte.
4. The commands the workflow references resolve to the vendored tinyspec
   extension (v1.0.0) — no new command definitions are introduced.
5. The harness-docs-map guard stays green (any path named in rules must exist).

## Plan

1. Author `.specify/workflows/tinyspec/workflow.yml` following the speckit
   workflow's schema (schema_version 1.0; inputs: task description +
   integration; steps per Requirement 2).
2. Register it: `specify workflow add .specify/workflows/tinyspec/workflow.yml`
   (or the path form the CLI expects for a local install); verify with
   `specify workflow list` and `specify workflow info tinyspec`.
3. Amend the "Below spec size" section of `.claude/rules/speckit-workflow.md`
   to mention the registered workflow id.
4. Full suite + ruff in the worktree; commit; PR.

## Tasks

- [x] Write `workflow.yml` for the tiny lane
- [x] Register via `specify workflow add` from the local path
- [x] Verify `specify workflow list` / `info tinyspec` output
- [x] Amend the rule section to name the workflow
- [x] Suite + ruff green; PR opened

## Done When

- [x] All tasks checked off
- [x] `specify workflow list` shows both lanes
- [x] Suite passes (incl. harness-docs-map guard); no lint errors
