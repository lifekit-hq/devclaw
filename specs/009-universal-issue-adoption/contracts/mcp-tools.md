# MCP Tool Contracts: Universal Issue Adoption

Two tools on the devclaw MCP surface (`devclaw/server/tools.py`). Types are the
FastMCP tool signatures; returns are JSON strings.

## `regrade_intake` (existing — semantics widened, signature unchanged)

```
regrade_intake(project_id: str, issue_url: str) -> str
```

- **Accepts**: any **open** issue on the registered project's repo — devclaw
  intake format (structured sections honored, exactly as today) or any
  hand-written format (title + body become the ask).
- **Rejects loudly** (`ToolError`): unknown project; project without a GitHub
  `repo_url`; unreachable/unreadable issue; issue not in `OPEN` state.
- **Effects**: readiness label (`devclaw-ready` / `needs-refinement`) lands on
  the issue as source-of-truth state; verdict mirror comment posted (same for
  every trigger path — clarify ruling); opposite label swapped out. Never
  edits the issue, never changes provenance.
- **Fail-closed**: any grading failure lands `needs-refinement`, never ready.
- **Returns**: `{issue_url, project_id, repo, readiness}` (unchanged shape).
- **Cognition**: exactly one `claude` call (via the OAuth-only seam), zero when
  rejected before grading.

## `grade_backlog` (new)

```
grade_backlog(project_id: str) -> str
```

- **Does**: lists the project's open issues (one `gh` call, ≤200, PRs
  excluded), partitions by readiness-label presence, grades up to
  `BULK_GRADE_CAP = 20` pending issues priority-band-first (`P0`…`P5`, then
  unlabeled; oldest first within a band) through the identical single-issue
  path. One issue's failure never stops the batch.
- **Rejects loudly** (`ToolError`): unknown project; no GitHub `repo_url`;
  listing failure (a `gh` error is a loud reject, not an empty sweep).
- **Never**: continues automatically, persists progress (re-invocation
  re-derives the pending set from labels), runs on the heartbeat, adds the
  `devclaw-intake` label, or dispatches anything.
- **Returns**: the BulkGradeReport (see `../data-model.md`) — every listed
  open issue accounted for by URL in exactly one bucket: `graded_ready`,
  `graded_needs_refinement`, `failed[{url, reason}]`, `skipped_already_graded`,
  `not_yet_graded`, plus `cap` and `listing_limit`.
- **Cognition**: ≤ cap `claude` calls; **zero** when nothing is pending
  (all-graded backlog ⇒ "nothing to grade" report, no cognition, listing call
  only).
