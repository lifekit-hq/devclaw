# TinySpec: eng-health "fix now" bin — first application of constitution IX

**Issue**: none (the 2026-09-06 engineering audit + `docs/audits/eng-health.md` seed baseline)
**Branch**: fix/eng-health-fix-now
**Date**: 2026-09-06
**Status**: done
**Complexity**: small

## What

Four findings the eng-health ratchet put in the "fix now" bin, plus one the
audit had binned as "spec" that Denys moved here (2026-09-06 park note): the
admission lint fails open. Every item is inside the five software domains of
constitution IX (verdict of record, protocol) or is a pure de-duplication.

1. **Admission lint fails CLOSED.** `judge_undecided` swallowed every
   exception and a greedy brace regex turned prose into `{}` — either way the
   goal was "admitted without it". It is the one cognition gate whose failure
   landed on the permissive side, at the seam that produces unclosable goals.
   Also found while fixing: creation passed the RAW `_evaluator_caller`
   attribute, which is `None` on a fresh process until the first done-gate
   eval — so production skipped the check ("no cognition caller configured")
   for the first goal created after every restart.
2. **`flip_cause` declared in the done-gate schema.** The parser requires it
   in pinned mode (spec 035 FR-011); only the Python-appended pinned block
   ever named it. The template's JSON schema now carries the key.
3. **`blocking` is derived, never read.** The review-gate parser derives the
   blocker/major subset from `issues`; `format_feedback` read it back off the
   dict as if it were a model key. One helper (`_blocking_issues`) is now the
   single definition, used by validate, feedback and the per-file union.
4. **One `extract_json`.** `devclaw/llm_call.py` already held the canonical
   tolerant extractor; evaluator, triage and intake readiness carried
   byte-identical copies differing only in the exception class. They import
   the leaf and translate `PlannerError` at their own call site. Admission
   lint's fifth (greedy, fence-blind) extractor is gone with item 1.
5. **`_parse_retry_after` runner ↔ loom: pinned, not collapsed.** The runner
   is stdlib-only and cannot import devclaw (spec 011); loom is the leaf
   substrate and cannot import the sandbox harness; the wheel ships only
   `devclaw/`. So the vendored copy stays, and a tripwire pins it identical
   to the host (patterns, unit table, answer on a corpus). Rejected: a third
   shared file baked into the image — a Dockerfile change plus a rebuild for
   a 12-line function is heavier than the guard, and the guard is what the
   "keep in sync" comment always needed.

## Context

| File | Role |
|------|------|
| `devclaw/goal/admission_lint.py` | `AdmissionLintError`; `judge_undecided` raises on caller failure / unreadable reply; uses `llm_call.extract_json` |
| `devclaw/goal/service.py` | creation catches `AdmissionLintError` → `ValueError` (nothing persisted); passes the LIVE `_evaluator()` caller |
| `devclaw/goal/evaluator.py`, `devclaw/goal/triage.py`, `devclaw/intake_readiness.py` | local `extract_json` deleted; import the leaf; translate `PlannerError` |
| `devclaw/quality/__init__.py` | `_blocking_issues` — one definition; `format_feedback` derives from `issues` |
| `devclaw/prompts/goal-evaluator.md` | `flip_cause` in the clause schema |
| `devclaw/loom/limits.py` | comment names the sync tripwire |
| `tests/test_goal_tick.py` | `test_admission_lint_fails_closed_on_a_malformed_judge_reply` (fail-closed gate class, extends the existing admission tests) |
| `tests/test_runner_limits.py` | `test_vendored_retry_after_parser_matches_the_host` (pause/brake class) |
| `tests/test_goal_evaluator.py`, `tests/test_review_gate.py` | import moved to the leaf; feedback test feeds `issues` (symmetric ratchet) |

## Requirements

1. A malformed judge reply (no JSON object, wrong shape, missing key,
   malformed entry) or a raising caller refuses goal creation with an
   actionable `ValueError` naming the cause; nothing is persisted.
2. An absent caller (`None`) still skips with the loud `note` — the only
   deliberate skip, a deployment fact.
3. `python evals/measure_eng_health.py --compare docs/audits/eng-health.json`
   shows `parser_keys_missing_from_schema` 2 → 0 and `duplicate_body_groups`
   2 → 1 (the pinned runner pair remains, by design).
4. No cognition-caller behaviour changes beyond the exception class seen at
   each call site.

## Plan

1. admission_lint + service (items 1) with the guard test.
2. Prompt schema key (item 2); `_blocking_issues` (item 3).
3. Collapse the three extractors onto `llm_call` (item 4).
4. Runner sync tripwire + loom comment (item 5).
5. Suite, ruff, mypy, eng-health delta.

## Tasks

- [x] Write tinyspec
- [x] Admission lint fails closed + live caller at creation
- [x] `flip_cause` in the done-gate schema
- [x] `_blocking_issues` single definition
- [x] One `extract_json`
- [x] Runner ↔ loom retry-after sync tripwire
- [x] Full suite + `ruff check .` + `mypy` green; eng-health delta recorded in the PR

## Done When

- [x] All tasks checked off
- [x] Malformed / raising judge → creation refused, nothing persisted (named test green)
- [x] Runner and loom `_parse_retry_after` pinned identical (named test green)
- [x] eng-health: `parser_keys_missing_from_schema` = 0, `duplicate_body_groups` = 1
- [x] Full suite green, ruff + mypy clean
