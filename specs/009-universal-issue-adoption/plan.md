# Implementation Plan: Universal Issue Adoption

**Branch**: `009-universal-issue-adoption` | **Date**: 2026-08-18 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/009-universal-issue-adoption/spec.md`

## Summary

Make the intake readiness grade accept any open GitHub issue on a registered
project — not only issues the intake doorway itself wrote — and add a separately
named, batch-capped bulk verb that grades a project's ungraded open backlog.
The fail-closed grading core (`grade_and_label`), the readiness prompt, and the
label/comment mechanics are all reused untouched; the change is confined to
**what the grader can read** (format-tolerant issue parsing in `regrade`) and
**how it can be invoked** (a new `grade_backlog` MCP tool that loops the
existing single-issue path over a label-derived pending set).

## Technical Context

**Language/Version**: Python 3.11 (existing `devclaw` package)

**Primary Dependencies**: `gh` CLI (existing `GhCli` adapter), `claude` via the
existing OAuth-only cognition seam (`intake_readiness.default_caller`), FastMCP
(existing `@mcp.tool` surface)

**Storage**: none new — the GitHub readiness label remains the source-of-truth
state (006 FR-007); no new tables, no progress records

**Testing**: pytest, fully stubbed (FakeGh / injected fake `claude_caller`);
extend `tests/test_intake.py`

**Target Platform**: Linux server (devclaw host process, layers 1–3)

**Project Type**: single Python package — MCP surface + intake module

**Performance Goals**: bulk invocation ≤ `BULK_GRADE_CAP` (20) cognition calls,
priority-first; zero cognition when nothing is ungraded

**Constraints**: zero-token idle guard untouched (no tick-path change);
fail-closed grading unchanged; intake-format issues behave byte-identically
(spec SC-003)

**Scale/Scope**: backlogs up to the `gh issue list` page (200 open issues);
~3 functions touched/added in `devclaw/intake.py`, 1 new MCP tool, ~1 docstring
update in `devclaw/server/tools.py`

## Constitution Check

*GATE: evaluated pre-Phase-0 and re-checked post-design — PASS on both.*

| Principle | Verdict | Evidence |
|---|---|---|
| I. OAuth only | PASS | Reuses `intake_readiness.default_caller()` through the existing cognition seam; no new spawn site. |
| II. Model-agnostic worker | PASS | Layer 1–3 host change only; worker harness untouched. |
| III. Zero-token idle | PASS | Both verbs run only on explicit MCP invocation; no heartbeat/tick change; existing `FakeClaude.calls == 0` guard tests unaffected. Bulk spends zero cognition when the pending set is empty (the list is a plain `gh` query). |
| IV. Single writer to state | PASS | No task/goal state touched. The only mutations are GitHub readiness labels + mirror comments — the source-of-truth location 006 established. |
| V. Fail-closed verification | PASS | `grade_and_label` remains the single fail-closed choke point; format tolerance widens its *input*, never its pass criteria. |
| VI. Loud over silent | PASS | Bulk returns a complete per-issue accounting incl. the ungraded remainder; closed/unreachable issues reject loudly; the 200-issue listing page bound is stated in the report. |
| VII. Fix the class | PASS | The fix is "the grader reads the universal unit of work in wild formats", not finance-sentry support. |

No violations — Complexity Tracking not needed.

## Project Structure

### Documentation (this feature)

```text
specs/009-universal-issue-adoption/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions + grounding evidence
├── data-model.md        # Phase 1 — entities (pending set, report, verdict flow)
├── quickstart.md        # Phase 1 — end-to-end validation guide
├── contracts/
│   └── mcp-tools.md     # Phase 1 — regrade_intake (widened) + grade_backlog (new)
└── tasks.md             # Phase 2 (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
devclaw/
├── intake.py            # MODIFIED: view_issue (title/body/state read),
│                        #   format-fallback in regrade, list_open_ungraded,
│                        #   grade_backlog orchestrator, BULK_GRADE_CAP
├── intake_readiness.py  # UNCHANGED (verified: empty done_when already renders
│                        #   "(none provided)"; rubric judges intent from the ask)
├── prompts/intake-readiness.md   # UNCHANGED (verified against rubric)
└── server/tools.py      # MODIFIED: regrade_intake docstring (any open issue),
                         #   NEW @mcp.tool grade_backlog(project_id)

tests/
└── test_intake.py       # EXTENDED: adoption fallback, closed-issue reject,
                         #   intake-format regression (SC-003), bulk cap/report/
                         #   resume/zero-cognition cases

docs/                    # SWEEP: any doc describing regrade_intake as
                         #   intake-format-only gets fixed in the same PR
                         #   (+ INDEX.md currency tags)
```

**Structure Decision**: everything lands in the existing `devclaw/intake.py` +
`devclaw/server/tools.py` seam — the same files 006 shipped in. No new modules:
the bulk verb is an orchestrator over existing pieces, exactly the shape of the
already-shipped `recover_pending_grades` sweep.

## Design (what each change is)

1. **Issue read widens to title+body+state** — `GhAdapter`/`GhCli` gain
   `view_issue(repo, issue) -> {title, body, state} | None` (one
   `gh issue view --json title,body,state` call). `regrade` switches to it; the
   now-unused body-only `read_issue` is deleted (no other callers — verified).
   A non-OPEN state raises `IntakeError` loudly (adoption targets open work).

2. **Format-fallback in `regrade`** — after `parse_issue_fields`, if `what` is
   empty (no `## What` section): the ask becomes `title + "\n\n" + body`,
   `done_when=""`, `context=""`. The existing prompt renders empty `done_when`
   as `(none provided)` and its rubric already requires a verifiable intent from
   the ask itself — no prompt change (grounded in research.md). Issues WITH
   intake sections take the exact existing path — SC-003's byte-identical
   regression is asserted by test.

3. **Pending-set lister** — `GhCli.list_open_ungraded(repo)`: one
   `gh issue list --state open --limit 200 --json url,labels,createdAt` call
   (PRs excluded by `gh issue list` by construction), partitioned client-side
   into already-graded (carries either readiness label) vs pending — the same
   label-derived, no-second-store pattern as `list_intake_awaiting_grade`.

4. **Bulk orchestrator** — `intake.grade_backlog(registry, project_id, *, gh,
   claude_caller)`: list once → partition → sort pending by priority label band
   (P0 < P1 < … < unlabeled) then `createdAt` ascending → grade the first
   `BULK_GRADE_CAP = 20` through the existing `regrade` path (one issue's
   failure never stops the batch — recovery-sweep convention) → return a report
   naming every open issue: `graded_ready` / `graded_needs_refinement` /
   `skipped_already_graded` / `not_yet_graded` (+ per-issue failure reasons).
   No persistence, no continuation: re-invocation re-derives the pending set.

5. **MCP surface** — `regrade_intake` keeps its name and signature (docstring
   now says: any open issue, any format); new `@mcp.tool grade_backlog
   (project_id: str)` returning the report as JSON. Naming per clarify ruling:
   the batch spend must be requested by name.

## Test Plan (named regression tests)

- `test_regrade_adopts_plain_issue_without_what_section` — hand-written issue
  grades via title+body fallback, lands a readiness label + mirror comment.
- `test_regrade_intake_format_issue_behavior_unchanged` — SC-003: structured
  issue produces identical prompt inputs/outcomes to pre-change behavior.
- `test_regrade_rejects_closed_issue_loudly` — non-OPEN state ⇒ `IntakeError`.
- `test_grade_backlog_caps_batch_and_reports_remainder_without_continuing` —
  cap honored, priority-first order, remainder named, no further calls.
- `test_grade_backlog_skips_graded_and_spends_zero_cognition_when_none_pending`
  — fake caller count == 0 on an all-graded backlog.
- `test_grade_backlog_resumes_by_rederiving_pending_from_labels` — second
  invocation grades exactly the remainder; no progress store consulted.
- `test_grade_backlog_one_issue_failure_never_stops_the_batch` — a mid-batch
  grade crash lands needs-refinement/failure-reason for that issue only.
- Existing zero-token guard tests stay green untouched (Constitution III).
