# Implementation Plan: Error-Issue Schema & Single Filing Doorway

**Branch**: `014-issue-doorway` | **Date**: 2026-08-24 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/014-issue-doorway/spec.md`

## Summary

One host-side, zero-LLM module — `devclaw/issue_doorway.py` — becomes the only
code path that turns a machine-found problem into a GitHub issue. It renders a
fixed, versioned, machine-parseable body (source, fingerprint, evidence,
expected-vs-actual, severity, proposed done-when), deduplicates by fingerprint
against a `machine_issues` ledger table in the state store, reopens closed
issues as marked recurrences, and fails loud (structured outcome + problems
catalog row) when GitHub is unreachable. The legacy self-issue-filing path
(`goal/self_issue.py`) migrates onto the doorway in US3, after which an AST
structural guard holds "exactly one machine-finding issue writer" the same way
`tests/test_config_single_doorway.py` holds the env-var doorway.

## Technical Context

**Language/Version**: Python 3.11 (repo baseline; mypy-gated)

**Primary Dependencies**: stdlib only (`dataclasses`, `re`, `json`) + the
existing `procutil.run` → `gh` CLI seam. No new packages.

**Storage**: SQLite (`devclaw.db`) — new `machine_issues` table owned by
`StateStore` (single-writer invariant IV), migration in
`devclaw/state_store/schema.py`.

**Testing**: pytest, fully stubbed (fake `GhAdapter`, `tmp_path` stores);
AST structural guard mirroring `tests/test_config_single_doorway.py`.

**Target Platform**: Linux host (VPS) — host-side only; the sandbox carries no
GitHub credential and never files (spec assumption, unchanged).

**Project Type**: Existing single-package backend; one new root module beside
`intake.py` (the human doorway) and `task_change.py` (the same
"one mechanical answer" shape).

**Performance Goals**: N/A — a handful of `gh` calls per filing at cycle-close
/ settle edges. Zero LLM calls (FR-007).

**Constraints**: Zero-token idle guard untouched; fail-loud, never-silent
(FR-006); issue bodies gradeable by the spec-009 format-tolerant intake loop
without grading changes (FR-008).

**Scale/Scope**: ~1 new module (~300 lines), ~60 lines of store, ~80 lines of
US3 migration, 3 test modules. Two PRs: PR1 = US1+US2 (schema + doorway +
dedup), PR2 = US3 (catalog migration + structural guard).

## Constitution Check

*GATE: evaluated pre-Phase-0 and re-checked post-design — PASS on both.*

- **I. OAuth only** — PASS. The doorway shells `gh` with the host's
  `GITHUB_TOKEN` credential exactly as `goal/self_issue.py` does today; no
  `ANTHROPIC_*` involvement anywhere.
- **II. Model-agnostic worker layer** — PASS. Host-side only; no worker skill,
  hook, or runner change. The sandbox still cannot file issues.
- **III. Zero-token idle** — PASS. The doorway is mechanical (FR-007): pure
  render/parse functions + `gh` subprocess. It is invoked from edges that
  already do subprocess work (cycle close, settle paths); nothing new runs on
  an idle tick.
- **IV. Single writer to state** — PASS, and strengthened. The new
  `machine_issues` ledger is written only through `StateStore` methods; the
  problems catalog keeps its existing `set_problem_issue` linkage written by
  the same store. US3 removes the last second writer of machine-finding
  issues; SC-004's guard is the views-never-read-back move applied to filing.
- **V. Verification fails closed** — N/A (no gate semantics change).
- **VI. Loud failure over silent degradation** — PASS; FR-006 *is* this
  principle. Evidence truncation carries an explicit marker (bounded coverage
  says so out loud).
- **VII. Fix the class, not the instance** — PASS; the spec exists to replace
  N per-mechanism filings with one class-level doorway.

No violations → Complexity Tracking stays empty.

## Project Structure

### Documentation (this feature)

```text
specs/014-issue-doorway/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions + rationale
├── data-model.md        # Phase 1 — entities + ledger table
├── quickstart.md        # Phase 1 — validation guide
├── contracts/
│   └── issue-schema.md  # Phase 1 — the versioned issue-body contract
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
devclaw/
├── issue_doorway.py         # NEW — MachineFinding, render/parse, file_finding()
├── state_store/
│   ├── schema.py            # + machine_issues table
│   └── machine_issues.py    # NEW — ledger mixin (get/upsert by repo+fingerprint)
└── goal/
    └── self_issue.py        # US3 — issue_body/create path delegates to the doorway

tests/
├── test_issue_doorway.py            # US1+US2: render/parse round-trip, dedup, fail-loud
├── test_issue_doorway_migration.py  # US3: catalog files through doorway, linkage intact
└── test_issue_doorway_single_writer.py  # SC-004 AST guard
```

**Structure Decision**: single new root module beside the human doorway
(`intake.py`); ledger as a store mixin following the existing
`state_store/problems.py` pattern; no new package.

## Phase 0 → research.md (decisions locked)

See [research.md](./research.md). Headlines: in-band version via HTML comment
metadata line + canonical `##` sections; dedup source of truth = local SQLite
ledger (not GitHub search); recurrence-after-close = comment-and-reopen
(matches existing `reopen_issue` semantics, one thread of history); FR-008
satisfied by spec-009's format-tolerant grading — no grading change needed
(verified in `devclaw/intake.py::regrade`).

## Phase 1 → data-model.md, contracts/, quickstart.md

Generated. The issue-body contract is the externally visible interface
(consumed by the intake grader, the owner, spec 015's validator and the
post-deploy smoke); the `file_finding()` call contract is internal but
documented in data-model.md for the two migrating/incoming producers.
