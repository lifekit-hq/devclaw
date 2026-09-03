# Implementation Plan: Verification ownership

**Branch**: `feat/032-verification-ownership` | **Date**: 2026-09-03 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/032-verification-ownership/spec.md`
(clarified 2026-09-03: Q1 C, Q2 B, Q3 A).

## Summary

The project's own verification environment becomes the verdict of record and the worker
never edits a project's gate inputs. Four shippable increments plus a doctrine amendment,
in this order: (1) the existing CI reader (`devclaw/goal/remote_checks.py`) is relocated
from *after* the done-gate evaluator to *before* the done-check review is even dispatched,
keyed to the PR head, with `pending` becoming a zero-token `mechanical:ci` hold and the
merge requiring the same green head; (2) the worker's `BLOCKED:` hand-back is typed so an
environment deficiency (`BLOCKED: env — <item>`) becomes a project-wide `mechanical:env`
hold that heals when the environment changes, a catalog row, and self-filed devclaw work;
(3) `ChangeSet` learns what kind of thing each changed path is and one always-hard gate
fails any gate-input edit or committed binary; (4) the scorecard reports human
interventions per achieved goal; then (5) constitution Principle V, CLAUDE.md and the
architecture doc drop "post-merge human review is the backstop". US4 (project-declared
environment provisioning) ships only its manifest surface here and is planned in a
follow-up revision after US1–US3 have a live track record (Q1 = C). Details and
alternatives: [research.md](./research.md).

## Technical Context

**Language/Version**: Python 3.13 (host, `devclaw/`), Python in the sandbox runner
(`runner/runner.py`), markdown worker skills (`runner/skills/`).
**Primary Dependencies**: FastMCP (layer 1), SQLite via the existing `StateStore`/`GoalStore`,
`gh` CLI on the host for every GitHub read (delivery, mergeability, remote checks), docker
(layer 4, untouched here).
**Storage**: SQLite `devclaw.db` — two new `goal_status` columns, one new table
`goal_interventions`, new `meta` rows under the spec-030 `env_cap_probe:` prefix.
**Testing**: pytest, fully stubbed (`FakeClaude`, `FakeEngine`, `FakeRemoteChecker`,
`ScriptedMerge`, the ACP fake agent); tripwire-net rule (tests only for invariant classes).
**Target Platform**: the VPS instance (compose service `devclaw-devclaw-mcp-1`), sandbox
image built from `.sandcastle/Dockerfile`.
**Project Type**: single Python service + in-sandbox runner (existing layout).
**Performance Goals**: zero LLM calls on every new path (holds, heals, red rollups); one
to two `gh` reads per done proposal and per merge, each under a 20 s timeout.
**Constraints**: zero-token idle (Principle III); fail-closed gates (V); single writer
(IV); OAuth fence untouched (I); worker skills stay plain markdown, one home (II).
**Scale/Scope**: ~10 registered projects, ~70 goals; the new reads add at most a few
`gh` calls per goal per day.

## Constitution Check

*GATE: evaluated before Phase 0; re-evaluated after Phase 1 design (below).*

| principle | status | note |
|---|---|---|
| I. OAuth only | pass | no new spawn site; `gh` reads use the host's existing token posture |
| II. Model-agnostic worker layer | pass | the typed hand-back is a text line the runner parses; skills edited in their one home; the fake-agent regression gains a `script_blocked_env` case |
| III. Zero-token idle | pass | every new branch (hold, heal, red rollup, env deficiency) is asserted `FakeClaude.calls == 0`; rollup reads run only on the done-proposal/merge paths and the throttled heal |
| IV. Single writer | pass | new columns and table are written by `GoalStore`/`StateStore` only; markdown views untouched |
| V. Verification fails closed; done is a proposal | **amended (FR-013)** | strengthened, not weakened: `unknown` stops approving; the human-review-backstop clause is replaced; the change is stated in the constitution in the same arc (version 2.6.0) |
| VI. Loud failure | pass | holds name the checks/items; the gate names the paths; a hung `gh` now times out loudly instead of hanging the tick |
| VII. Fix the class | pass | the spec exists to retire instance fixes (post-evaluator CI check, Playwright-deps rationale, `CI_GATE_MODE`) |
| Development workflow | pass | speckit pipeline; tripwire tests only; increments = reviewable PRs; the whole spec is the commitment, US4 deferral is stated out loud in the spec |

Post-design re-check: unchanged; the one amendment is FR-013, planned as its own
increment (5) so the constitution and CLAUDE.md move in the same PR as the code that
retires the doctrine line.

## Project Structure

### Documentation (this feature)

```text
specs/032-verification-ownership/
├── spec.md              # clarified
├── plan.md              # this file
├── research.md          # R1–R8 decisions with file:line anchors
├── data-model.md        # rollup fact, status columns, worker cap rows, ChangedPath, interventions
├── quickstart.md        # stubbed run + live shakedown walk-through
├── contracts/
│   ├── worker-result-line.md
│   ├── change-set-paths.md
│   └── ci-rollup-and-scorecard.md
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks output (not created here)
```

### Source Code (repository root)

```text
devclaw/
├── goal/
│   ├── remote_checks.py      # check_pr(), required-check filter, timeouts; check_branch + CI_GATE_MODE removed
│   ├── tick_donegate.py      # rollup read at the top of _open_done_gate; merge-side re-read; post-evaluator block removed
│   ├── tick.py               # pending_done_proposal fast path; _autoheal_ci in the blocked branch
│   ├── tick_settle.py        # env-deficiency fork beside the worker-block Problem; column resets
│   ├── tick_guards.py        # _block_on_env_deficiency (writes the worker cap row, reuses _block_on_env_cap)
│   ├── tick_context.py       # RemoteChecker signature (pr_url)
│   ├── models.py             # pending_done_proposal, ci_green_head; mechanical:ci in the taxonomy
│   ├── state.py / state_status.py / store/status.py   # DDL + migration + projections for the two columns
│   ├── state_interventions.py (new) + store facade     # goal_interventions writer/reader
│   ├── service.py            # the four verbs record interventions; steer refusal unchanged
│   └── env_cap.py            # worker:* rows, env_ref, ci:definition capability
├── queue/settle.py           # env marker branch; ChangeSet.paths capture; _ChangeClassGate in both chains
├── task_change.py            # ChangedPath, classify_path, glob tables, in-scope extraction
├── quality/task_gates.py     # _ChangeClassGate;  quality/gate_policy.py: ALWAYS_HARD += change_class
├── delivery/__init__.py      # non-worker commit interventions at delivery
├── telemetry.py              # interventions block; format_scorecard; cli.py scorecard
├── project_manifest.py       # environment block (parse only) + docs/reference/devclaw-manifest.schema.json
├── doctor/checks_instance.py # three new checks
└── prompts/goal-evaluator.md # gate-input paths are never evidence
runner/
├── runner.py                 # _classify_block, payload fields, _RETURN_CONTRACT text
└── skills/_writes-code/      # 50-repo-gate-conflict.md rewritten; 20-verify-gate-coverage.md, 90-commit.md edited
    onboard/00-onboard.md     # CI workflow as a sibling artifact (Q3)
tests/
├── test_goal_tick.py         # remote-checks class rewritten for the new position; blocked-kind entries; ci heal
├── test_merge_on_close.py    # head-moved-after-green case
├── test_env_cap_admission.py # worker-reported row holds + heals; ci:definition
├── test_task_retry.py        # env kind not retried (parametrized)
├── test_runner_blocked.py / test_runner_acp.py / acp_fake_agent.py   # typed block contract
├── test_materialize_gate.py  # change_class gate is always-hard, fails closed, no retry
├── test_gate_policy.py       # ALWAYS_HARD membership
├── test_runner_skills.py     # no --no-verify in the bundle (structural guard)
└── test_doctor.py            # seeded faults for the two columns and the table
docs/  CLAUDE.md  .specify/memory/constitution.md   # FR-013/014 amendments, env-vars, INDEX tags
```

**Structure Decision**: no new packages; every change lands in the module that owns the
seam today (layer map: settle path = layer 4, done-gate/merge = layer 2, runner = layer 5,
`task_change.py` = the one definition of change). The only new module is the
`goal_interventions` state mixin, following the `state_problems.py` shape.

## Increments (each one reviewable PR; ordered by dependency and value)

1. **US1 — CI rollup as the fact of record** (`remote_checks.check_pr`, relocation to
   `_open_done_gate`, `mechanical:ci` hold + heal, merge-side re-read, two status columns,
   doctor checks, `gh` timeouts, `CI_GATE_MODE` retired, env-vars doc). Tests: the six
   remote-checks tests rewritten for the new position, blocked-kind stamping, heal, merge
   head-moved, zero-token on hold, doctor seeded faults.
2. **US2 — typed environment outlet** (runner classification + contract text, settle
   marker + catalog row, goal-layer hold via worker cap rows with `env_ref`, admission
   consults them, `ci:definition` capability, onboarding skill gains the CI workflow).
   Tests: runner parser + fake-agent script, retry class, env-cap admission class.
3. **US3 — change classification** (`ChangedPath`, `classify_path`, in-scope extraction,
   `_ChangeClassGate` in both chains, `ALWAYS_HARD`, brief + evaluator rule, skills
   rewrite). Tests: materialize-gate class extended, gate-policy membership, skills
   structural guard, brief presence/absence.
4. **US5 — interventions metric** (`goal_interventions` table + writers, delivery commit
   attribution, scorecard block, CLI/format, doctor check). Tests: doctor seeded fault
   only (the metric is ordinary behavior).
5. **Doctrine** (constitution 2.6.0, CLAUDE.md, architecture.md, Dockerfile comment,
   spec 030 status line, INDEX currency tags) plus the manifest `environment` schema/parse
   surface for US4 (R8). Tests: `test_harness_docs_map` stays green; manifest parse loud
   on malformed block (extends the existing manifest class test).
6. **US4 — declared environment provisioning**: follow-up plan revision, not in this arc.

## Complexity Tracking

No constitution violations to justify. The one amendment (Principle V) is a
strengthening recorded in the same arc. Persisted-state additions (2 columns, 1 table,
`meta` rows) each carry a doctor check and seeded-fault test per spec 016 FR-014.
