# TinySpec: import contracts — the modular monolith's boundaries, CI-enforced

**Issue**: none (Denys, 2026-09-06: "when something grows enough it should be separate - at least a modular monolith"; vault `projects/devclaw/plan.md` "Modular direction")
**Branch**: refactor/import-contracts
**Date**: 2026-09-06
**Status**: implemented — PR open (suite 1429 passed / 5 skipped; 7 contracts KEPT, 0 broken)
**Complexity**: small (tooling adoption + three one-edge moves; no behavior change)

## What

The layer order in `CLAUDE.md` and the "leaf" status of `loom`, `llm_call`,
`config`, `dispatch_gate`, `state_store` and `runner/` are folder convention
plus one subprocess test. This adopts **import-linter** (the standard tool —
constitution IX "adopt standard practice") as a CI gate beside ruff and mypy,
declares those boundaries as contracts, and retires the three breaches a dry
run found (2026-09-06, 150 files / 507 edges, everything else already KEPT):

| breach | one-edge fix |
|---|---|
| `llm_call -> cognition` (l.682, the `claude_with_model` shim binds through the cognition seam it sits below) | `claude_with_model` moves to `cognition.py` (its docstring already says the swap point is there); the 7 callers re-point `from ..cognition import claude_with_model` |
| `state_store.control -> dispatch_gate` (l.353, the store imports a gate's default) | `DEFAULT_SCHEDULE` moves to `config.py` ("one home, one default"); the store and `dispatch_gate` both read it from there |
| `queue.settle -> validation_loop -> intake -> goal.issue_ref` (layer 4 reaches layer 2 for one git helper) | `repo_slug` moves from `intake.py` to `task_git.py` (the git-wrapper home); `intake` and `validation_loop` import it there |

Not extraction: packages stay in one repo, one distribution. Standalone
packaging waits for the N=2 trigger (a second real consumer), ruled the same
day — a package with one consumer is a directory with a release process, and
a cross-repo change cannot be one devclaw goal.

## Context

| File | Role |
|------|------|
| `pyproject.toml` | `[tool.importlinter]` root_packages `devclaw`+`runner`, `exclude_type_checking_imports`; 7 contracts (below); `import-linter` added to `dev` extras |
| `.github/workflows/ci.yml` | `lint-imports` step in the Lint job, same fresh-venv shape as ruff/mypy |
| `devclaw/cognition.py`, `devclaw/llm_call.py` + 7 callers (`goal/{evaluator,summary,triage,service}.py`, `quality/{__init__,reachability}.py`, `intake_readiness.py`) | the `claude_with_model` move |
| `devclaw/config.py`, `devclaw/dispatch_gate.py`, `devclaw/state_store/control.py`, `tests/test_dispatch_gate.py` | the `DEFAULT_SCHEDULE` move (5 test asserts re-point) |
| `devclaw/task_git.py`, `devclaw/intake.py`, `devclaw/validation_loop.py` | the `repo_slug` move |
| `tests/test_llm_call_leaf.py` → `tests/test_llm_call_oauth_strip.py` | the two leaf-ness tests are DELETED (symmetric ratchet — the contract replaces them); the two OAuth-strip tests stay, docstring rewritten |
| `CLAUDE.md` (run-the-tests block + layer map), `docs/architecture.md` (five-layers section), `docs/INDEX.md` | "the order is CI-enforced" stated once each |

## Requirements

1. Contracts, all KEPT on the PR: `runner` forbids `devclaw`; `loom` forbids
   `devclaw.*`; `config` forbids `devclaw.*` except `_env_loader`;
   `dispatch_gate` forbids `devclaw.*` except `config`; `llm_call` forbids
   `devclaw.*` except `loom.*`/`config`; `state_store` forbids `devclaw.*`
   except `config`; layers (top→bottom) `server | cli` › `doctor` › `goal` ›
   `task_queue : queue` › `delivery | quality` › `engine` › `state_store` ›
   `loom | config`. No `ignore_imports` debt lines: the three breaches are
   fixed, not waived.
2. Zero behavior change: the suite's count is unchanged minus the two deleted
   leaf tests; no prompt, gate, or state shape moves.
3. Unlayered root modules stay unconstrained in this PR. Four cycles among
   them are known (`task_queue<->task_notify` — a queue mixin living outside
   `queue/`; `host_resources<->project_registry`; `goal<->trend_detector`,
   the "typed as object to avoid a cycle" comment in `goal/tick.py`;
   `goal<->intake`) and are the ratchet's next rungs: each is assigned to a
   layer in the PR that next touches it (eng-health fix-now bin), never all at
   once. Rejected: waiving them as `ignore_imports` now — a waiver list is the
   debt going silent, the mypy adoption ratchet (zero at adoption, tighten
   later) is the precedent.
4. Rejected: a homegrown AST guard (the `test_config_single_doorway` shape) —
   a devclaw-specific mechanism needs a reason the standard tool cannot give,
   and import-linter also checks transitive chains and function-level imports.

## Plan

1. `[tool.importlinter]` + dev extra; run `lint-imports` locally — expect the
   three breaches and nothing else.
2. The three moves; rerun — expect 7 KEPT.
3. Delete the two leaf tests; re-point the 5 schedule asserts.
4. CI step; docs; `pytest`, `ruff check .`, `mypy`, `lint-imports` green; PR.

## Tasks

- [x] Write tinyspec
- [x] pyproject contracts + dev extra
- [x] `claude_with_model` → `cognition.py`, 7 callers re-pointed
- [x] `DEFAULT_SCHEDULE` → `config.py` (as `DEFAULT_RUN_SCHEDULE`; `dispatch_gate` now imports config, its leaf contract says so)
- [x] `repo_slug` → `task_git.py`
- [x] `tests/test_llm_call_leaf.py` trimmed (renamed to what it still pins); `tests/test_dispatch_gate.py` re-pointed
- [x] CI `lint-imports` step; CLAUDE.md, architecture.md, INDEX.md
- [x] Suite + ruff + mypy + lint-imports green

## Done When

- [x] `lint-imports`: 7 contracts KEPT, no debt `ignore_imports` (the 8 present are the leaves' declared downward edges), gated in CI
- [x] Suite green; no behavior change in the diff
- [x] Vault `plan.md` "Modular direction" names this spec (done 2026-09-06)
