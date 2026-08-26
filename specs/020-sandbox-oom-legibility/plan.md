# Implementation Plan: Sandbox OOM Legibility and Prevention

**Branch**: `020-sandbox-oom-legibility` | **Date**: 2026-08-26 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/020-sandbox-oom-legibility/spec.md`

## Summary

Make sandbox OOM kills legible (classified deterministic, one adapted
re-dispatch, actionable block reason), survivable (workload processes carry a
self-raised OOM score so the killer takes them, never the agent), visible
(the engine declares the enforced memory/CPU allocation into the sandbox
env; worker guidance bounds tooling by it), and right-sized (per-project
registry overrides mirroring ADR 0005's `sandbox_image`, accounted for by
launch admission and validated loudly at write time). Design decisions and
rejected alternatives: [research.md](./research.md).

## Technical Context

**Language/Version**: Python 3.11 (host layers), Python 3 stdlib-only in-sandbox runner, bash for the shield script

**Primary Dependencies**: none new — stdlib `subprocess` preexec, cgroup v2 file reads, existing docker CLI argv

**Storage**: SQLite — one new `goal_status` column (`envcap_redispatches`), two new `projects` columns (`sandbox_memory`, `sandbox_cpus`; auto-migrated from the `_OVERRIDE_STR_FIELDS` tuple)

**Testing**: pytest, fully stubbed (no docker/claude); live OOM behavior proven via the live-shakedown lane, per spec Assumptions

**Target Platform**: Linux VPS, docker with cgroup v2; sandbox container runs non-root (`USER agent`, uid 1000)

**Project Type**: existing 5-layer service — changes land in layers 4 (queue/engine) and 5 (runner) plus the layer-1 registry write surface

**Performance Goals**: zero added per-task docker calls; evidence capture = two cgroup file reads at terminal moments

**Constraints**: no new container privileges/capabilities (FR-006); zero-token idle untouched (no cognition anywhere in this spec); fail-closed semantics preserved (FR-004/SC-005)

**Scale/Scope**: ~10 source files + 5 goal-state lockstep seams + tests/docs; 4 independently-shippable increments (one per user story)

## Constitution Check

| Principle | Verdict | Note |
|---|---|---|
| I. OAuth only | PASS | No auth surface touched; env forwards are sizing values only. |
| II. Model-agnostic worker layer | PASS | Shield uses `BASH_ENV` (generic bash, works for any ACP agent spawning shell children) + a bash script in the image; guidance lands in the ONE skill home (`runner/skills/`); no vendor tool-wiring. |
| III. Zero-token idle | PASS | No LLM calls added anywhere; classification is string matching; evidence is file reads. |
| IV. Single writer to state | PASS | New goal counter rides `GoalStatus` through the existing CAS'd transitions; registry fields go through the registry's own write choke point; no new writers. |
| V. Verification fails closed | PASS | OOM classification only converts a FAILURE into a better-explained FAILURE; no gate consultation changes; conservative no-evidence path is byte-identical to today (FR-004). |
| VI. Loud failure over silent degradation | PASS (it's the point) | Actionable reasons, write-time rejection of unadmittable overrides, honest block kinds. |
| VII. Fix the class, not the instance | PASS | Nothing lifekit-common-specific; all mechanisms are per-class. |

Re-check after Phase 1 design: unchanged — no violations, Complexity Tracking empty.

## Project Structure

### Documentation (this feature)

```text
specs/020-sandbox-oom-legibility/
├── spec.md
├── plan.md              # this file
├── research.md          # D1–D6 decisions + rejected alternatives
├── data-model.md        # new fields, marker grammar, state transitions
├── contracts/
│   ├── runner-oom-marker.md   # runner error marker + evidence contract
│   └── mcp-project-sizing.md  # update_project / console sizing params
├── quickstart.md        # validation guide (stubbed suite + live shakedown)
└── checklists/requirements.md
```

### Source Code (repository root)

```text
devclaw/
├── queue/
│   ├── settle.py          # US1: _SANDBOX_OOM_MARKER fast-fail branch
│   └── admission.py       # US4: parameterized _mem_can_launch/_mem_commit_launch
├── task_queue.py          # US4: _sandbox_sizing resolution in the pump loop
├── engine/
│   ├── __init__.py        # US4: EngineRequest.sandbox_memory/sandbox_cpus
│   └── sandcastle.py      # US3: -e declaration family; US4: sizing kwargs in _build_docker_args
├── state_store/rows.py    # US1: sandbox_oom failure-class rule
├── goal/
│   ├── models.py + state.py + state_status.py + store/status.py + store/view_migration.py
│   │                      # US1: envcap_redispatches (5 lockstep seams)
│   ├── tick.py            # US1: OOM-adapted failure-context brief branch
│   ├── tick_dispatch.py   # US1: env-cap block (kind + honest reason)
│   └── tick_settle.py     # US1: counter reset on productive settle
├── project_registry.py    # US4: fields + _validate_sandbox_memory (write-time admittability)
├── server/tools/projects.py + server/routes/projects.py   # US4: write edges
└── doctor/checks_instance.py  # US4: sizing-override admittability check (spec 016 FR-014)

runner/
├── runner.py              # US1: cgroup evidence + marker; US2: preexec on _run_verify/_run_one_hook/_mise_run; US2/US3: agent env allowlist (BASH_ENV, sizing vars)
└── skills/_writes-code/40-verify-iterate.md   # US3: bounded-tooling guidance

.sandcastle/Dockerfile     # US2: ship /opt/devclaw/oom-shield.sh

tests/
├── test_task_retry.py             # US1 marker class (pattern: prompt-too-long cases)
├── test_goal_tick.py              # US1 adapted brief + env-cap block
├── test_sandbox_isolation.py      # US3/US4 argv (pattern: _build_docker_args pure tests)
├── test_sandbox_image_override.py # US4 template: registry → queue → EngineRequest → argv
├── test_dispatch_memory_admission.py  # US4 per-task admission accounting
├── test_runner*.py                # US1 evidence emission, US2 preexec presence
└── test_doctor*.py                # US4 seeded-fault doctor check
```

**Structure Decision**: existing repo layout; no new modules except the shield
script baked into the sandbox image. Increment = user story = one reviewable
PR, in order US1 → US2 → US3 → US4 (US3's env declaration is introduced by
US3 even though US4 later makes it per-project — US4 only changes the value's
source).

## Complexity Tracking

(no constitution violations — table intentionally empty)
