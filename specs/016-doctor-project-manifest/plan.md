# Implementation Plan: Instance Doctor + Per-Project Manifest

**Branch**: `016-doctor-project-manifest` | **Date**: 2026-08-24 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/016-doctor-project-manifest/spec.md`

## Summary

Two interlocking mechanisms. (1) A read-only, zero-LLM `doctor` diagnostic —
one new check package (`devclaw/doctor/`) surfaced through a new MCP tool
module and a CLI subcommand — that codifies the post-redeploy checklist as
named checks over the existing stores (migration meta keys, legacy row
shapes, credential file expiry, skills-bundle resolvability, raw run-schedule
key, usage pause, registry/goal link integrity, workspace preflight).
(2) A repo-owned `devclaw.json` manifest read through one new doorway module
(`devclaw/project_manifest.py`) that supplies per-project declarations —
strictness default, surface kind, `verify_cmd`, stack markers, boilerplate
revision — consumed at the existing dispatch-time and settle-time seams, with
every post-run read pinned to `pre_run_sha` via `git show`.

## Technical Context

**Language/Version**: Python 3.11 (existing repo toolchain)

**Primary Dependencies**: stdlib only for new code (json, dataclasses,
subprocess-via-existing-git-helpers); FastMCP tool registration via the
existing `@mcp.tool` import-side-effect pattern

**Storage**: existing `devclaw.db` (SQLite, read-only access for doctor) +
`goal.yaml` files + the new in-repo `devclaw.json` per operated project.
Doctor persists nothing.

**Testing**: pytest, fully stubbed (tests/goal_fakes.py — `FakeClaude`,
`FakeEngine`, `register_tmp_project`, `seed_goal`, `seed_marker_repo`);
zero-token guard shape `assert evaluator.calls == 0`; AST structural guards
(single-doorway, views-never-read-back, env-vars doc sync)

**Target Platform**: Linux host (VPS + WSL2 dev), same as the rest of devclaw

**Project Type**: additions to the existing 5-layer service — a new read-only
projection package + a doorway module + thin layer-1 surfaces

**Performance Goals**: full doctor run < 30 s on the live instance (SC-001);
pure filesystem stats + SQLite SELECTs + a bounded number of `git` subprocesses

**Constraints**: zero cognition calls on every doctor path (SC-003); no tick-path
hook; no state mutation; no new `DEVCLAW_*` env vars (none needed — all inputs
come from existing config accessors)

**Scale/Scope**: single-digit projects, tens of goals — no pagination concerns;
report must stay deterministic for unchanged state

## Constitution Check

*GATE: evaluated against constitution v2.4.0 — PASS (pre-Phase-0 and re-checked post-Phase-1).*

- **I. OAuth only**: PASS. Doctor makes no cognition calls and introduces no
  key path. The credential check *stats and parses* `.credentials.json` /
  `.claude.json` and reads `CLAUDE_CODE_OAUTH_TOKEN` presence — it never
  invokes `claude` and never exports a key.
- **II. Model-agnostic worker layer**: PASS. `devclaw.json` is host-read
  config, not worker instructions; `runner/skills/` remains the one home for
  worker-kind instructions and this spec adds no second copy. The doctor
  skills-bundle check reuses the runner's own pure path resolver
  (`runner.runner._skill_paths_for_root`, import-guarded → `unknown` finding
  if unimportable) rather than forking the resolution logic.
- **III. Zero-token idle**: PASS. Doctor is operator-invoked only (MCP tool +
  CLI); nothing hooks `goal/tick.py` or its chain. A named zero-token guard
  test asserts `FakeClaude.calls == 0` across a full doctor run. Manifest
  reads happen only on dispatch/settle paths that already do git work — never
  on idle ticks.
- **IV. Single writer to state**: PASS. Doctor is read-only by construction
  (report dataclasses, no store writes). It reads goal state from
  `GoalStore`/SQLite, never by parsing markdown views (the
  views-never-read-back guard stays green). Devclaw writes `devclaw.json`
  only through the existing reviewable-PR onboard path; no runtime writer.
- **V. Verification fails closed; "done" is a proposal**: PASS. `gate_policy`
  is untouched; strictness resolution adds a *source* tier (goal-explicit >
  manifest > default) ahead of the existing consequence logic, and anything
  unrecognized still resolves to BLOCK. A malformed manifest rejects dispatch
  loudly (never a silent default). Post-run manifest reads are pinned to
  `pre_run_sha` so the worker cannot loosen its own gates (FR-009).
- **VI. Loud failure over silent degradation**: PASS — it is the feature's
  organizing principle. A check that cannot run reports `unknown` with the
  error as evidence; a healthy instance is reported affirmatively.
- **VII. Fix the class, not the instance**: PASS — each check names a drift
  *class* with a seeded-fault test per class (SC-002); the doctor-check-per-
  shape-change rider (FR-014) makes the class coverage self-extending.

No violations → Complexity Tracking not needed.

## Project Structure

### Documentation (this feature)

```text
specs/016-doctor-project-manifest/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── doctor-tool.md   # MCP tool + CLI output contract
│   └── devclaw-manifest.schema.json   # the published manifest JSON Schema
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
devclaw/
├── doctor/                        # NEW — read-only check engine (no layer reached through)
│   ├── __init__.py                #   run_doctor(...) facade + re-exports
│   ├── model.py                   #   Finding / DoctorReport dataclasses, verdict enum
│   ├── checks_instance.py         #   instance checks (meta keys, legacy shapes, credential,
│   │                              #   skills bundle, run schedule, usage pause)
│   └── checks_project.py          #   per-project checks (preflight, links, manifest,
│                                  #   revision, marker integrity, scaffold drift)
├── project_manifest.py            # NEW — the devclaw.json doorway: SCHEMA_VERSION,
│                                  #   BOILERPLATE_REVISION, Manifest dataclass, fail-loud
│                                  #   parse, load at worktree HEAD or at an arbitrary ref
├── server/tools/doctor.py         # NEW — @mcp.tool doctor (thin: binds _state singletons)
├── server/tools/__init__.py       # EDIT — import + re-export the new module
├── cli.py                         # EDIT — `devclaw doctor` subcommand (normal reg+goals path)
├── goal/engine.py                 # EDIT — verify_cmd precedence gains manifest tier (3 sites)
├── goal/store/base.py             # EDIT — strictness persisted only when explicit; loader
│                                  #   keeps None for absent (raw tier for live resolution)
├── goal/evaluator.py              # EDIT — done-gate reads resolved strictness
├── goal/tick_settle.py            # EDIT — slice guardrail reads resolved strictness
├── queue/settle.py                # EDIT — settle-time strictness + browser-gate surface read
│                                  #   manifest at pre_run_sha
├── quality/task_gates.py          # EDIT — _browser_gate_failure accepts surface override
├── speckit_setup.py               # EDIT — install PR seeds devclaw.json when absent
├── server/tools/_common.py        # EDIT — dispatch preflight rejects malformed manifest loudly
docs/
├── INDEX.md                       # EDIT — new doc rows + currency tags
└── reference/
    └── devclaw-manifest.md        # NEW — manifest reference (schema, precedence, examples)
CLAUDE.md                          # EDIT — FR-014 rider in Conventions
runner/skills/onboard/00-onboard.md  # EDIT — devclaw.json named human-owned, agent must not author it
tests/
├── test_doctor.py                 # NEW — per-class seeded-fault tests + zero-token guard
├── test_project_manifest.py       # NEW — doorway parse/precedence/fail-loud tests
├── test_manifest_gates.py         # NEW — pre_run_sha pinning + surface override + verify_cmd tier
└── (edits: test_onboard_speckit.py, test_env_vars_doc_sync.py untouched — no new env vars)
```

**Structure Decision**: `devclaw/doctor/` is a sibling read-only projection
package (like `quality/`), never imported by the tick chain; layer 1 stays
thin (the tool module only binds `_state` singletons and serializes).
`project_manifest.py` sits at repo-module level beside `config.py`,
mirroring its single-doorway doctrine for a different axis (per-repo file
vs. env).

## Slicing (unit of review; the whole spec is the commitment)

- **PR 1 / US1**: `devclaw/doctor/` + MCP tool + CLI + instance checks +
  the per-project checks that need no manifest (preflight, dangling links,
  missing project_id). Ships alone; immediately useful on the VPS.
- **PR 2 / US2**: `project_manifest.py` doorway + consumption seams
  (strictness tier, verify_cmd tier, browser-gate surface, malformed-rejects-
  dispatch) + install-PR seeding + schema doc.
- **PR 3 / US3**: revision constant comparison, marker-integrity and
  `.specify/` scaffold drift checks wired into doctor's project section,
  CLAUDE.md rider, docs sweep.
