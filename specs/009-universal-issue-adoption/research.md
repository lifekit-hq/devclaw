# Phase 0 Research: Universal Issue Adoption

All findings are grounded in the current code (read 2026-08-18, main @ 41d838f).
No NEEDS CLARIFICATION markers remained after the clarify session; this file
records the design decisions and the code evidence they rest on.

## D1 — Where format tolerance lives: the `regrade` parse, nothing deeper

**Decision**: The only format-sensitive point in the whole grading path is
`devclaw/intake.py::regrade`, which raises when `parse_issue_fields` finds no
`## What` section (`intake.py:456`). The fallback (`what = title + body`,
`done_when = ""`, `context = ""`) is added there; `grade_and_label`,
`_apply_readiness_label`, and the evaluator are untouched.

**Rationale**: `grade_and_label` already takes plain strings and is the
fail-closed choke point — feeding it differently-sourced strings changes
nothing about its semantics. Keeping the fallback at the parse site keeps the
SC-003 guarantee trivially true: an issue *with* intake sections never reaches
the fallback branch.

**Alternatives considered**: a general "issue normalizer" module — rejected as
machinery for one branch; teaching `parse_issue_fields` itself to fall back —
rejected because that function's contract is "parse the shape `issue_body`
writes" and it is also the honest signal for "this is not intake format."

## D2 — No prompt change needed (verified, not assumed)

**Decision**: `devclaw/prompts/intake-readiness.md` is unchanged.

**Rationale (evidence)**: `build_prompt` already renders an empty `done_when`
as `(none provided)` (`intake_readiness.py:107`), and the template grades
`done_when` explicitly as "context for grounding, NOT a checklist to grade" —
the ready rubric requires a *verifiable intent* found in the ask itself. A
hand-written issue with no stated completion intent therefore fails the third
rubric element and lands `needs-refinement` — exactly spec FR-002 — with no new
prompt line. Per the cognition-prompts rule ("bar for a line is *changes model
behaviour*"), adding an "issues may be free-form" line would be emphasis, not
behavior.

**Alternatives considered**: a dedicated adoption prompt variant — rejected:
two prompts for one verdict drift apart; the rubric is already format-agnostic.

## D3 — Reading title + state: widen the issue read to `view_issue`

**Decision**: Replace `GhAdapter.read_issue` (body-only) with
`view_issue(repo, issue) -> {title, body, state} | None` via
`gh issue view --json title,body,state`. `regrade` rejects non-`OPEN` state
loudly (`IntakeError`) before any cognition.

**Rationale**: the fallback ask needs the title; the closed-issue edge case
needs the state; one `gh` call yields all three. `read_issue` has **no callers
outside `intake.py`** (grepped 2026-08-18), so replacing it is clean — keeping
a dead body-only variant would be the kind of residue the amputation doctrine
exists to prevent.

**Alternatives considered**: a second title-only call — rejected (two
subprocesses for one read); keeping `read_issue` alongside — rejected (dead
code the day `regrade` switches).

## D4 — Bulk pending set: label-derived, mirroring the recovery sweep

**Decision**: New `GhCli.list_open_ungraded(repo)`: one
`gh issue list --state open --limit 200 --json url,labels,createdAt`, then a
client-side partition into already-graded (has `devclaw-ready` OR
`needs-refinement`) vs pending — the exact pattern of the shipped
`list_intake_awaiting_grade` (`intake.py:203`), minus its `devclaw-intake`
label filter. `gh issue list` excludes PRs by construction. The 200-issue page
bound is stated in the bulk report (Principle VI), not silently applied.

**Rationale**: 006 already ruled (and this spec re-ruled) that the labels are
the one source of truth for gradedness; deriving the pending set from them
makes bulk idempotent and resumable with zero new state. The recovery sweep is
the proven reference implementation of the loop shape (best-effort per issue,
one failure never stops the rest).

**Alternatives considered**: server-side `-label`-negation filtering — `gh` has
no NOT-label filter (already documented at `intake.py:203`); a progress table —
rejected in the spec (Rejected Alternatives).

## D5 — Priority-first ordering within the batch

**Decision**: Sort pending issues by priority-label band — `P0` … `P5` by
number, unlabeled last — then `createdAt` ascending; grade the first
`BULK_GRADE_CAP = 20`.

**Rationale**: matches the repo-wide backlog convention (P0–P5 labels, FIFO
within band) that spec 007 also adopted for claim order — one triage order
across the arc. Ordering is a convenience, not a contract (spec Assumptions).

## D6 — Bulk verb naming and shape on the MCP surface

**Decision**: `@mcp.tool grade_backlog(project_id: str)` in
`devclaw/server/tools.py`, returning the JSON report. `regrade_intake` keeps
its name/signature; only its docstring widens ("any open issue on the
project's repo, any format").

**Rationale**: clarify ruling — the batch spend must be requested by name; an
optional-argument mode was explicitly rejected. Keeping `regrade_intake`'s name
avoids breaking the existing MCP caller surface (the OpenClaw waiter's tool
list) for zero benefit; its semantics only widen.

**Alternatives considered**: renaming to `grade_issue` — rejected: a surface
rename ripples through waiter configs and docs for aesthetics; deprecation
aliases are the sprawl the polish gate is against.

## D7 — What the bulk report must contain

**Decision**: The report accounts for **every open issue** returned by the
listing, keyed by URL: `graded_ready[]`, `graded_needs_refinement[]`,
`skipped_already_graded[]`, `not_yet_graded[]` (the capped remainder), plus
`failed[]` with per-issue reasons (a grade that errored — which still landed
`needs-refinement` on the issue via the fail-closed path, or couldn't be read
at all). Also `cap`, `listing_limit` (200), and the project/repo echoed back.

**Rationale**: spec FR-007 ("complete per-issue accounting … never silent");
SC-004 ("every open issue accounted for by name").

## D8 — Recovery sweep interaction (no change needed)

**Decision**: `recover_pending_grades` keeps filtering on the `devclaw-intake`
label and is NOT extended to adopted issues.

**Rationale**: the restart gap it closes is specific to intake filing (receipt
returned, in-process async grade lost). Adoption has no async grade — both
verbs grade synchronously within the tool call — so an interrupted bulk run
leaves plain ungraded issues, which the next explicit `grade_backlog`
invocation re-derives (spec FR-008). Extending the boot sweep to all ungraded
issues would make serve-start spend cognition proportional to every registered
repo's backlog — an unasked-for background burst that contradicts the batch-cap
ruling (operator-triggered chunks).
