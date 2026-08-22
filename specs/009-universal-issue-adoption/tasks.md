# Tasks: Universal Issue Adoption

> **SHIPPED — every task below is complete.** The boxes were never ticked during
> execution, which made this feature look not-yet-complete to any reader scanning
> for unchecked items — including the worker brief, which selects "the smallest
> not-yet-complete specs/NNN-*/ (its tasks.md still has unchecked items)".
> Ticked retroactively 2026-08-22 after verifying the feature in the code.

**Input**: Design documents from `/specs/009-universal-issue-adoption/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/mcp-tools.md, quickstart.md

**Tests**: INCLUDED — the repo's testing rule mandates a named regression test
per behavior change; plan.md names them. Write each story's tests first and see
them fail before implementing.

**Organization**: Both user stories are P1 (clarify ruling: bulk is in the first
slice). US2 builds on US1's widened `regrade`, so the phases run sequentially —
this is one PR, not parallel workstreams.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

**Purpose**: Isolated branch per the repo's git workflow (never on `main`).

- [x] T001 Create worktree + branch: `git worktree add /tmp/claude-1000/-home-dsdevqq-projects-devclaw/57d2614d-4a1f-4dcf-8340-b15d0907efbb/scratchpad/wt-009 -b feat/universal-issue-adoption origin/main`; verify `git branch --show-current` there, then verify the pytest import path per `.claude/rules/testing.md` (`.venv/bin/python -c "import devclaw; print(devclaw.__file__)"` must print the worktree path when run from the worktree root)

---

## Phase 2: Foundational (blocking both stories)

**Purpose**: The widened issue read both stories depend on (research.md D3).

- [x] T002 In `devclaw/intake.py`: replace `GhAdapter.read_issue` with `view_issue(repo, issue) -> Optional[dict]` returning `{title, body, state}` (Protocol + `GhCli` impl via one `gh issue view --json title,body,state` call; delete `read_issue` — no callers outside this module, verified in research.md D3); switch `regrade` to `view_issue` (behavior otherwise unchanged in this task)
- [x] T003 Update every test fake implementing the gh adapter in `tests/test_intake.py` (and any in `tests/test_intake_readiness.py`) from `read_issue` to `view_issue`; full intake test files stay green before moving on

**Checkpoint**: suite green with the widened read, byte-identical grading behavior.

---

## Phase 3: User Story 1 — Adopt a hand-written issue (Priority: P1) 🎯 MVP

**Goal**: `regrade_intake` grades ANY open issue on a registered project — intake
format honored as today, otherwise title+body become the ask; closed issues
reject loudly. (spec US1; research.md D1–D3)

**Independent Test**: stub a hand-written issue (no `## What`) → fallback grades
it and lands a label + mirror comment; stub an intake-format issue → identical
prompt inputs/outcome as before the change; stub a CLOSED issue → loud error.

### Tests for User Story 1 (write first, watch them fail)

- [x] T004 [US1] Add named regression tests in `tests/test_intake.py`: `test_regrade_adopts_plain_issue_without_what_section` (fallback ask = title+body, `done_when` empty ⇒ prompt renders `(none provided)`, readiness label + mirror comment land), `test_regrade_intake_format_issue_behavior_unchanged` (SC-003: structured issue produces identical evaluator inputs/outcomes as pre-change), `test_regrade_rejects_closed_issue_loudly` (state != OPEN ⇒ `IntakeError`, zero cognition calls)

### Implementation for User Story 1

- [x] T005 [US1] In `devclaw/intake.py::regrade`: after `parse_issue_fields`, when `what` is empty fall back to `what = title + "\n\n" + body`, `done_when = ""`, `context = ""` (delete the "has no readable '## What' section" rejection); reject non-`OPEN` state loudly with `IntakeError` BEFORE any cognition; intake-format issues must not touch the fallback branch
- [x] T006 [US1] In `devclaw/server/tools.py`: update the `regrade_intake` docstring to the widened contract per `specs/009-universal-issue-adoption/contracts/mcp-tools.md` (any open issue, any format; grading is not promotion — provenance untouched)

**Checkpoint**: US1 tests + full intake tests green — single-issue adoption works.

---

## Phase 4: User Story 2 — Bulk backlog onboarding (Priority: P1)

**Goal**: a separately named `grade_backlog(project_id)` MCP tool grades up to
`BULK_GRADE_CAP = 20` ungraded open issues priority-first and returns a complete
per-issue report; resumable by re-derivation, zero cognition when nothing is
pending. (spec US2; research.md D4–D8)

**Independent Test**: stub a mixed backlog (graded/ungraded/many) → one call
grades exactly the cap priority-first, reports every issue in one bucket; second
call grades exactly the remainder; all-graded backlog spends zero cognition.

### Tests for User Story 2 (write first, watch them fail)

- [x] T007 [US2] Add named regression tests in `tests/test_intake.py`: `test_grade_backlog_caps_batch_and_reports_remainder_without_continuing` (cap honored, P0-band-then-oldest order, `not_yet_graded` named, no extra calls), `test_grade_backlog_skips_graded_and_spends_zero_cognition_when_none_pending` (fake caller count == 0), `test_grade_backlog_resumes_by_rederiving_pending_from_labels` (second invocation grades exactly the remainder, no progress store), `test_grade_backlog_one_issue_failure_never_stops_the_batch` (mid-batch crash ⇒ that issue in `failed[]` with a reason, rest still graded)

### Implementation for User Story 2

- [x] T008 [US2] In `devclaw/intake.py`: add `GhAdapter`/`GhCli.list_open_ungraded(repo)` — one `gh issue list --state open --limit 200 --json url,labels,createdAt` call, client-side partition into already-graded vs pending by readiness-label presence (mirror `list_intake_awaiting_grade`'s never-raises shape, but a listing failure in the bulk verb itself is a LOUD reject per the contract)
- [x] T009 [US2] In `devclaw/intake.py`: add `BULK_GRADE_CAP = 20` and `grade_backlog(registry, project_id, *, gh=None, claude_caller=None) -> dict` — partition, sort pending by priority band (`P0`…`P5`, unlabeled last) then `createdAt` asc, grade first cap via the existing `regrade` path (one issue's failure never stops the batch — recovery-sweep convention), return the BulkGradeReport per `specs/009-universal-issue-adoption/data-model.md` (every listed issue in exactly one bucket; `cap` + `listing_limit` stated)
- [x] T010 [US2] In `devclaw/server/tools.py`: add `@mcp.tool grade_backlog(project_id: str) -> str` per `specs/009-universal-issue-adoption/contracts/mcp-tools.md` (loud `ToolError` on unknown project / missing repo_url / listing failure; returns the report as JSON)

**Checkpoint**: both stories independently green; zero-token guard tests untouched.

---

## Phase 5: Polish & Ship

- [x] T011 [P] Docs honesty sweep: grep `docs/` + `README.md` for `regrade_intake` / intake-doorway claims made stale by the widened contract and the new tool; fix in place and update currency tags in `docs/INDEX.md` (same-PR rule)
- [x] T012 Full suite from the worktree: `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q` — pass count ≥ the 2229 baseline (tip 41d838f) + the new named tests
- [x] T013 Ship: conventional commit (`feat(intake): universal issue adoption — format-tolerant regrade + capped grade_backlog`, body names the regression tests, `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`), push, `gh pr create` with the spec linked and the standard PR footer; remove the worktree after merge

---

## Dependencies & Execution Order

- **T001 → T002 → T003** strictly sequential (foundation).
- **US1 (T004–T006)** after Phase 2; T004 before T005 (test-first); T006 anytime after T005.
- **US2 (T007–T010)** after US1 (bulk loops the widened `regrade`); T007 before T008/T009; T010 after T009.
- **T011** parallel with Phase 4 onward [P]; **T012 → T013** last.

Single-file contention note: T005/T008/T009 all edit `devclaw/intake.py` and
T006/T010 both edit `devclaw/server/tools.py` — do them in task order, no
parallel edits.

## Implementation Strategy

MVP = Phase 1–3 (US1): single-issue adoption alone already unblocks
finance-sentry issue-by-issue. Phase 4 completes the clarified P1 scope (bulk in
first slice). One PR ships all phases — the slice is small (one module + one
tool surface + tests); splitting would create a two-PR stack for ~200 lines.
