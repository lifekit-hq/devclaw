# Data Model: Universal Issue Adoption

No new durable storage. The GitHub readiness label remains the single
source-of-truth state (006 FR-007); everything below is ephemeral, in-process
data flowing through one tool call.

## Entities

### IssueView (ephemeral — the widened issue read)

| Field | Type | Notes |
|---|---|---|
| `title` | str | Issue title as GitHub returns it |
| `body` | str | Raw body, may be empty |
| `state` | str | `OPEN` required; anything else ⇒ loud `IntakeError` (single-issue verb) / skipped-by-listing (bulk lists open only) |

Produced by `GhAdapter.view_issue` (replaces body-only `read_issue`; no other
callers existed).

### Ask (universal form — input to the unchanged grader)

| Field | Intake-format issue | Hand-written issue (fallback) |
|---|---|---|
| `what` | `## What` section | `title + "\n\n" + body` |
| `done_when` | `## Done when` section | `""` → prompt renders `(none provided)` |
| `context` | `## Context` section | `""` |

Fallback triggers **only** when `parse_issue_fields` yields an empty `what` —
intake-format issues never touch it (SC-003).

### ReadinessVerdict (existing, unchanged)

`ready: bool`, `missing: list[str]`, `rationale: str` — see
`intake_readiness.py`. Persisted as label + mirror comment by the existing
`_apply_readiness_label`.

### PendingSet (ephemeral — bulk partition)

Derived per invocation from one `gh issue list` call (open, ≤200, PRs excluded
by `gh`), partitioned by label presence:

| Bucket | Rule |
|---|---|
| `already_graded` | labels ∩ {`devclaw-ready`, `needs-refinement`} ≠ ∅ |
| `pending` | disjoint from both readiness labels |

`pending` is sorted priority-band-first (`P0` < `P1` < … < unlabeled), then
`createdAt` ascending. First `BULK_GRADE_CAP = 20` are graded; the rest become
`not_yet_graded`. Never persisted — re-derived on every invocation (resumable
by construction).

### BulkGradeReport (tool return value, JSON)

| Field | Type | Notes |
|---|---|---|
| `project_id` / `repo` | str | Echo of the target |
| `graded_ready` | list[url] | This batch |
| `graded_needs_refinement` | list[url] | This batch (incl. fail-closed landings) |
| `failed` | list[{url, reason}] | Grade attempted, no label landed (e.g. unreadable issue) |
| `skipped_already_graded` | list[url] | Zero cognition spent |
| `not_yet_graded` | list[url] | Beyond the cap — "run again to continue" |
| `cap` | int | `BULK_GRADE_CAP` (20) |
| `listing_limit` | int | 200 — the loudly-stated page bound (Principle VI) |

Invariant: every URL from the listing appears in exactly one bucket (SC-004).

## State transitions (on the GitHub issue — unchanged from 006)

```
(unlabeled) ──grade──▶ devclaw-ready ◀──re-grade──▶ needs-refinement
```

- Label add is the one load-bearing write; opposite-label removal and mirror
  comment are best-effort (existing `_apply_readiness_label`).
- Grading never adds `devclaw-intake`, never edits body/title, never touches
  provenance (spec FR-005/FR-006).
```
