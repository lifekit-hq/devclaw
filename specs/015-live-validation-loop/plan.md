# Implementation Plan: Live-Validation Loop

**Branch**: `015-live-validation-loop` (stacked on `014-issue-doorway`) | **Date**: 2026-08-24 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/015-live-validation-loop/spec.md`

## Summary

Three increments. **US1** makes acceptance scenarios executable ground truth by
tightening the three existing enforcement seams — the spec template, the
intake-readiness prompt's "verifiable intent" element, and the done-gate
prompt's structural enumeration; the browser gate already carries FR-002's
proof-of-execution semantics (a named regression test pins it to this spec).
**US2** adds a `validate_product` task kind: an agent-less runner branch boots
the repo-declared validation contract (a new nested `validation` key in
`devclaw.json`), runs the accumulated suites, and returns a machine
`validation_report`; the HOST (which holds the GitHub credential) maps each
failure to a spec-014 `MachineFinding` and files it through the doorway — no
PR, no commit, no gate verdict, zero LLM. **US3** adds the `qa` goal mode (a
per-repo goal that never self-advances and never terminates), the post-deploy
trigger (a layer-2 verb called from the deploy tool and the auto-deploy edge),
the read-only prod smoke, and the periodic cadence that ships OFF.

## Technical Context

**Language/Version**: Python 3.11 (host) + the runner's stdlib-only harness.

**Primary Dependencies**: none new. Playwright JSON reports (existing runner
plumbing) are the machine-readable suite format; the spec-014 doorway is the
filing target.

**Storage**: no new tables. Findings ride the 014 `machine_issues` ledger; run
records ride the existing goal log/events.

**Testing**: pytest, fully stubbed (FakeClaude, FakeEngine, fake gh); the
runner's validation branch is unit-tested directly (no docker).

**Target Platform**: Linux host + the per-task docker sandbox. The sandbox
runs `--network host` — a booted product can bind ports; the contract's boot
command owns picking a non-colliding port (documented in the contract).

**Project Type**: existing package; changes span prompts (layer 3 inputs),
manifest (host config doorway), queue/settle (layer 4), runner (layer 5),
goal (layer 2), delivery tools (layer 1 → 2 verb).

**Performance Goals**: a validation run fits the existing `TASK_TIMEOUT_S`
wall clock; suites cut by the cap are reported as explicit partial coverage.

**Constraints**: zero LLM during suite execution (FR-005) — the runner branch
never spawns the ACP agent; zero-token idle for the QA goal (FR-007);
fail-loud on missing contract/boot failure (FR-006); production untouched by
e2e (FR-009).

**Scale/Scope**: ~6 host modules touched + 1 runner branch + 2 prompt files +
1 template + tests. Ships as TWO PRs: PR-A (US1, independent — no doorway
dependency) and PR-B (US2+US3, stacked on `014-issue-doorway` because every
finding files through the doorway).

## Constitution Check

*GATE: evaluated pre-Phase-0 and re-checked post-design — PASS.*

- **I. OAuth only** — PASS. No new spawn sites; the runner's validation branch
  spawns subprocesses with the same stripped env as verify_cmd.
- **II. Model-agnostic worker layer** — PASS. The validation branch is
  agent-agnostic by construction (no agent is driven at all); no new worker
  skills; the contract is repo-declared data, not vendor wiring.
- **III. Zero-token idle** — PASS. `qa` goals never plan: the tick path for
  them is a pure no-op below the existing cheap checks; the deploy trigger and
  cadence trigger submit a mechanical task (no cognition). Named
  `FakeClaude.calls == 0` tests ship with US3.
- **IV. Single writer to state** — PASS. Task rows stay TaskQueue-owned; the
  QA goal rides `GoalStore`; findings ride the 014 ledger through the doorway.
- **V. Verification fails closed; "done" is a proposal** — PASS, carefully:
  the validator is NOT a gate — it emits intake, never verdicts (spec
  assumption confirmed). The done-gate change is prompt-side only, riding the
  existing structural axis and dial. The browser gate is untouched in
  semantics (FR-002 = existing behavior, pinned by test).
- **VI. Loud failure** — PASS: missing contract / boot failure / crashed suite
  each produce a filed finding + a loud run record (FR-006); partial coverage
  is stated, never silent.
- **VII. Fix the class, not the instance** — PASS: the finance-sentry
  scheduler scar is closed as a class (runtime wiring proven by booting the
  product), and the mechanism is domain-generic (contract-declared boot/suites).

**Mode note**: `GoalMode` gains `"qa"`. ADR 0003's dial is re-evaluation
cadence over ONE execution path; `qa` is the "never self-advances" point on
that dial — the execution path (sandboxed worker, settle, gates) is unchanged,
so this extends the dial without adding a second path. The constitution needs
no amendment (its Development Workflow / principles do not enumerate mode
values); recorded here per the grilled decision trail.

## Project Structure

### Documentation (this feature)

```text
specs/015-live-validation-loop/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions + rationale
├── data-model.md        # Phase 1 — contract, report, QA goal, finding mapping
├── quickstart.md        # Phase 1 — validation guide
├── contracts/
│   └── validation-contract.md  # devclaw.json `validation` key + report schema
└── tasks.md             # Phase 2
```

### Source Code (repository root)

```text
# PR-A (US1)
.specify/templates/spec-template.md      # acceptance scenarios must be executable
devclaw/prompts/intake-readiness.md      # element (c) → executable-test-expressible
devclaw/prompts/goal-evaluator.md        # structural enum + uncovered scenarios
tests/test_acceptance_executability.py   # prompt-content + SC-005 flow + FR-002 pin

# PR-B (US2+US3)
devclaw/project_manifest.py              # `validation` nested key + resolver
devclaw/state_store/rows.py              # TaskKind + "validate_product"
devclaw/goal/models.py                   # GoalMode + "qa"; QA_DONE_WHEN constant
devclaw/goal/validation.py               # NEW — layer-2: trigger_validation(), settle mapping → doorway, prod smoke
devclaw/goal/tick.py                     # qa goals: no advance, cadence-armed trigger
devclaw/queue/settle.py                  # validate_product: no materialize/deliver; workspace discard; report → goal layer
devclaw/engine/{__init__,sandcastle,host}.py  # payload carries `validation` contract
runner/runner.py                         # agent-less validate_product branch
devclaw/server/tools/{tasks,delivery,goals}.py  # kind exposure + deploy hook
tests/test_validate_product*.py, tests/test_qa_goal*.py
```

**Structure Decision**: one new layer-2 module (`goal/validation.py`) owns the
loop's host half (trigger + finding mapping + smoke) so layer 1 stays pure
protocol and layer 4 stays dispatch; everything else is seam edits.

## Phase 0 → research.md

All decisions grounded in the seam map (2026-08-24); see research.md D1–D10.

## Phase 1 → data-model.md, contracts/, quickstart.md

Generated. The externally visible contract is the `devclaw.json` `validation`
key + the runner's `validation_report` shape; findings reuse the spec-014
schema with `source="validator"` / `source="deploy_smoke"`.
