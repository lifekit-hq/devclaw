# Research — spec 014 (issue doorway)

No `NEEDS CLARIFICATION` markers survived the 2026-08-24 grill; the open
points the spec explicitly deferred to planning are resolved here, each
grounded in the existing code.

## D1 — Module location: `devclaw/issue_doorway.py` (root module)

**Decision**: one new root module, sibling of `intake.py` (the human doorway)
and `task_change.py` (the "one mechanical answer" precedent).

**Rationale**: the doorway is layer-agnostic host wiring used by cycle-close
(`goal/self_issue.py`), future spec-015 validator settle paths, and the deploy
smoke — putting it inside `goal/` or `delivery/` would force cross-layer
imports. `intake.py` already establishes the root-module-doorway shape.

**Alternatives considered**: a `devclaw/issues/` package (over-structure for
~300 lines); extending `intake.py` (rejected — human intent vs machine
findings are different contracts with different writers; mixing them blurs the
FR-002 guard).

## D2 — In-band schema version + fingerprint: metadata comment line

**Decision**: the body opens with a human blockquote provenance line followed
by one machine-metadata HTML comment:
`<!-- devclaw-machine-issue v1 fingerprint=<fp> source=<source> severity=<sev> -->`
then the canonical visible sections in fixed order: `## Source`,
`## Evidence`, `## Expected vs actual`, `## Severity`,
`## Proposed done-when`.

**Rationale**: FR-003 wants machine-parseable AND human-readable — the comment
is invisible on GitHub but trivially extractable (single regex, no
heuristics); the visible sections read as a normal issue. FR-004's version
rides the same line. Fingerprint in metadata (stable, greppable) mirrors the
existing `<sub>fingerprint: …</sub>` precedent in `goal/self_issue.py` but is
easier to parse.

**Alternatives considered**: YAML front-matter (GitHub renders it as a table
sometimes, as literal text otherwise — fragile); JSON block in a `<details>`
(duplicates every field, drifts from the prose).

## D3 — Dedup source of truth: local SQLite ledger, not GitHub search

**Decision**: a `machine_issues` table in `devclaw.db` keyed
`(repo, fingerprint)`, owned by `StateStore` (single-writer, invariant IV).
`file_finding()` consults it first; GitHub is never searched.

**Rationale**: GitHub search is eventually consistent and rate-limited — using
it as the dedup key source makes SC-002 (zero duplicates) probabilistic. The
instance is single-writer by construction (one VPS), so the local ledger is
authoritative. The problems catalog already proves this pattern
(`issue_number`/`issue_state` on the problem row).

**Alternatives considered**: `gh search issues` by fingerprint (consistency +
quota); reusing the problems table for all producers (wrong shape — problems
rows are gatherer signals with their own lifecycle; a validator finding is not
a problem row, and forcing it through one would blur the N1/#371 boundary the
spec explicitly preserves).

## D4 — Recurrence after close: comment-and-reopen

**Decision**: a fingerprint whose issue is closed and recurs is reopened with
a comment explicitly marked `**Recurrence** (regression)` carrying the fresh
evidence; the ledger flips back to `open`.

**Rationale**: US2 scenario 2 offered comment-and-reopen or linked follow-up,
one behavior chosen at planning. Reopen keeps the full history in one thread,
matches the existing `GhAdapter.reopen_issue` semantics the self-issue path
already ships, and keeps the intake loop's view simple (one issue = one root
cause, ever).

**Alternatives considered**: linked follow-up issue (splits history; doubles
the grader's work; invents a second issue for the same fingerprint, which is
exactly what SC-002 forbids for open issues and is confusing for closed ones).

## D5 — FR-008 (gradeable without grading changes): already true, verify by test

**Decision**: no change to `devclaw/intake.py` grading. Spec 009 made
`regrade` format-tolerant — any open issue's title+body is the ask. The
doorway's `## Proposed done-when` section gives the grader a concrete
completion contract to find. A named test files a doorway issue body through
the grading parser path to prove consumability (SC-003's stub half).

**Rationale**: FR-008 requires compatibility, not change (spec Out of Scope).

## D6 — Failure surfacing: structured outcome + problems-catalog row

**Decision**: `file_finding()` returns a `FilingOutcome`
(`action: filed|updated|reopened|failed`, `issue_number`, `reason`). On
failure it additionally records a problems-catalog row (category `delivery`…
existing `PROBLEM_CATEGORIES` fallback `other`, kind `issue_filing_failed`)
when a store is provided, and the caller's error path receives the failed
outcome — never an exception swallowed, never a silent drop (US1 scenario 3).
The finding itself is retained by the ORIGINATING surface (spec edge case:
retry is the origin's re-invocation on its next edge; the doorway itself does
not queue).

**Rationale**: mirrors the fail-loud-not-fatal contract of `GhCli` in
`goal/self_issue.py` (a filing failure never wedges the cycle edge) while
making the failure a first-class value instead of a stderr line.

## D7 — Evidence truncation: deterministic cap with explicit marker

**Decision**: evidence is capped at 6,000 characters, cut at the cap with a
trailing `… [truncated: N chars omitted]` marker. All other fields carry
smaller caps (title 240 chars, matching `issue_title` precedent).

**Rationale**: GitHub bodies cap at 65,536 chars; 6k evidence keeps the whole
body comfortably under it while staying humanly scannable. Deterministic (no
"smart" summarizing — that would be an LLM call, forbidden by FR-007).

## D8 — Labels: one doorway marker + caller pass-through

**Decision**: every doorway-filed issue carries `devclaw:machine-filed`;
callers pass their existing labels through unchanged (the migrated catalog
path keeps `devclaw:self-filed` + `class:<cat>` so Stage-2 pickup and the
console lifecycle keep working byte-identically).

**Rationale**: US3 requires the catalog's linkage to behave exactly as before;
labels beyond what filing needs are out of scope.

## D9 — Structural guard (SC-004): AST test over issue-creation call sites

**Decision**: `tests/test_issue_doorway_single_writer.py` walks every module
under `devclaw/` with `ast` and asserts that subprocess invocations of
`gh issue create` (and `GhAdapter.create_issue` implementations) exist only in
`devclaw/issue_doorway.py` and `devclaw/intake.py` (the human doorway, out of
this spec's scope by design). Pattern copied from
`tests/test_config_single_doorway.py`.

**Rationale**: SC-004 asks for the views-never-read-back move applied to
filing; an allowlist AST guard is the established house pattern for it.
