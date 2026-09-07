# Implementation Plan: An environment gap is filed when the pipeline says it is filed

**Branch**: `038-env-deficiency-filing-honesty` | **Date**: 2026-09-07 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/038-env-deficiency-filing-honesty/spec.md`

## Summary

The settle text promises a filing owned by a once-per-cycle edge that can
never fire for this class (the `mechanical:env` hold suppresses the recurrence
the 2-cycle filing bar requires). Move the filing to the seam that already
knows everything the issue needs — `_block_on_env_deficiency` in
`devclaw/goal/tick_guards.py`, where the project, the goal, the task and the
capability id are all in hand — route it through the existing issue doorway,
and make the log/hold/ping carry the real outcome. Layer 4 stops claiming an
action it cannot perform. No new table, no new issue writer, no cognition
call, no change to the hold, the ping marker or the heal.

## Technical Context

**Language/Version**: Python 3.11 (existing repo toolchain)

**Primary Dependencies**: none new — `devclaw/issue_doorway.py` (spec 014) and its `gh` adapter

**Storage**: none new — the doorway's existing `machine_issues` ledger and the `problems` table, both written through `StateStore`'s single writer

**Testing**: pytest, fully stubbed. `DEVCLAW_SELF_REPO` is unset in every test by default, so the filing path is a no-op unless a case sets it and injects a fake `gh` — no subprocess, no network

**Target Platform**: the deployed VPS instance (layers 2 and 4)

**Project Type**: internal harness machinery — MCP tool surface untouched

**Performance Goals**: one `gh` invocation per NEW environment gap, on an already-failed settle; zero on the idle path

**Constraints**: zero-token idle (Principle III) — the new work runs only inside a settle that has already observed a worker env block; env-gated so the default path spawns nothing

**Scale/Scope**: one seam, one new module, four touched files

## Constitution Check

- [x] **I. OAuth only** — no cognition call added; the doorway shells `gh` with a GitHub credential, never `ANTHROPIC_*` (the same seam self-issue filing already uses).
- [x] **II. Model-agnostic worker layer** — host-side only (layers 2 and 4); `runner/` untouched.
- [x] **III. Zero-token idle** — the filing runs inside the env-deficiency settle branch, which fires only after a worker reported a block. No tick-path or idle-path work; `FakeClaude.calls == 0` stays green and `DEVCLAW_SELF_REPO` unset means not even a subprocess (FR-006).
- [x] **IV. Single writer** — the ledger and the catalog are written through `StateStore` as today, reached by thin `GoalStore` passthroughs (FR-009); no view is read back.
- [x] **V. Verification fails closed** — no gate is touched. The `mechanical:env` hold is placed exactly as today and a filing failure cannot lift it (FR-005).
- [x] **VI. Loud failure** — the whole point: the outcome is stated (filed / already tracked / not filed and why), and a doorway failure leaves its catalog row (FR-007).
- [x] **VII. Fix the class** — the class is "a message asserts an action the pipeline did not perform"; #818's `NODE_AUTH_TOKEN` gap is the instance. The fix moves the action to the layer that states it and removes the deadlocked gate, so every future env gap inherits it.
- [x] **VIII. Cognitive guardrails vs structural invariants** — no guardrail added. This is bookkeeping honesty on an existing brake, not new reasoning.
- [x] **IX. Instruct thin, verify thick** — the gap closed is a missing **fact** on an operator-facing surface, not a new brake and not an instruction to the agent. Software's domain here is "the protocol": what the pipeline says it did must be what it did.

## Project Structure

### Documentation (this feature)

```text
specs/038-env-deficiency-filing-honesty/
├── spec.md      # the clarified spec, with the root cause and rejected alternatives
├── plan.md      # this file
├── tasks.md     # the two phases (US1 = this PR, US2 = P2)
└── checklists/  # created by the speckit script
```

### Source (repository root)

```text
devclaw/
├── goal/
│   ├── env_issue.py      # NEW — the env-gap → doorway filing edge + its outcome line
│   ├── tick_guards.py    # _block_on_env_deficiency calls it; the line rides log/hold/ping
│   └── store/base.py     # two thin ledger passthroughs beside record_problem/get_meta
└── queue/settle.py       # the env-block text stops claiming the filing
tests/test_env_cap_admission.py   # extends the worker-deficiency tripwire cases
```

## Slice map — the per-story read budget

- **US1 (P1, this PR)** — `devclaw/goal/env_issue.py` (new), `devclaw/goal/tick_guards.py`
  (`_block_on_env_deficiency`, `_block_on_env_cap`), `devclaw/goal/store/base.py`
  (passthroughs), `devclaw/queue/settle.py` (the `_WORKER_ENV_MARKER` suffix),
  `tests/test_env_cap_admission.py`, `docs/flows/task-execution.md` +
  `docs/reference/env-vars.md` + `docs/INDEX.md`.
  Constraints discovered: the catalog fingerprint is
  `fingerprint_for("block", "env_deficiency", item)` and the `item` string
  reaching the goal layer is the same one `settle.py` recorded — the two must
  not drift, so the fingerprint is computed from the shared helper and never
  re-invented. `_block_on_env_cap` already owns the log/transition/ping
  sequence and the `env_hold_notified` once-per-episode marker; the filing line
  is threaded in as an optional extra clause rather than duplicating that
  sequence. `GoalStore` deliberately has no `_state` reach-through, so the
  ledger needs `machine_issue_get` / `machine_issue_record` passthroughs (and
  `set_problem_issue` for FR-003).

- **US2 (P2, landed 2026-09-07)** — `devclaw/doctor/checks_instance.py` (a check beside
  `check_goal_status_env_hold_notified`), `tests/test_doctor.py`
  (`_run`/`_findings` unpack ONE finding per check id — mutate one registered
  workspace across shapes, never register a second project),
  `docs/runbooks/doctor.md` + `docs/INDEX.md`. Constraint found while building
  it: the check reads the ambient environment, so `DEVCLAW_SELF_REPO` must be
  cleared or the seeded fault reads OK on a configured host and the guard
  passes having checked nothing. The load-bearing clearing is at
  `tests/conftest.py` module level (it precedes every `devclaw` import and
  `config.self_repo()` reads the environment per call); the `delenv` in the
  doctor `env` fixture is local defence in depth, beside the OAuth and
  registry tokens it already clears.

## Load-bearing choices

- **Fingerprint = the catalog fingerprint.** One identity across the catalog
  row, the doorway ledger and the once-per-cycle filer, so a gap filed at the
  hold is recognised as already-filed at cycle close instead of opening a
  duplicate (FR-002/FR-003). Precedent: `self_issue.finding_from_problem`
  reuses the catalog fingerprint verbatim for exactly this reason.
- **The doorway, not a new writer.** `tests/test_issue_doorway_single_writer.py`
  structurally holds `gh issue create` to the doorway + human intake; a second
  path would break that guard and fork the issue schema (#630 class).
- **The outcome is a string the caller threads through, not a store write.**
  The hold text, the log line and the ping already exist and are written in one
  CAS'd sequence inside `_block_on_env_cap`; adding a column for "filing
  outcome" would be a second writer for a fact that is only ever read by a
  human.
- **Never-raises, and bounded.** `file_env_deficiency` swallows everything and
  degrades to a stated "NOT filed: <error>" line. The hold is the fact that
  protects the project's sessions; bookkeeping must not be able to break it
  (FR-005). It also runs under a wall-clock bound (`mergeability.GH_TIMEOUT_S`,
  the same one `remote_checks._gh` uses) because it is on the heartbeat sweep.
- **Filing before the transition, cap row before both.** The red `worker:<item>`
  capability row is written FIRST, so even if the `expect=status` CAS loses to a
  concurrent `steer_goal` and this tick's block is abandoned, the next tick's
  admission guard holds the project on the row that already exists. That is what
  makes it safe to keep the hold as ONE atomic write carrying the filing clause,
  instead of a block-then-amend pair.
